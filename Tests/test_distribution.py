import json
import pathlib
import plistlib
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Tools'))
import distribution as d


class DistributionTests(unittest.TestCase):
    def archive(self, root):
        info = plistlib.loads((d.ROOT / 'Resources/Info.plist').read_bytes())
        value = d.metadata(info)
        path = root / (value['stem'] + '.zip')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('libvpn.app/Contents/Info.plist', plistlib.dumps(info))
        return path, value

    def test_names_bind_version_and_architecture(self):
        info = {'CFBundleShortVersionString': '1.2.3-beta.1', 'LSMinimumSystemVersion': '14.0'}
        for arch in ('arm64', 'x86_64'):
            self.assertEqual(d.metadata(info, arch)['stem'], 'libvpn-1.2.3-beta.1-' + arch)
        for invalid in ('../other', '1.0\ncommand', '1/2', ''):
            with self.assertRaises(ValueError): d.metadata(dict(info, CFBundleShortVersionString=invalid), 'arm64')
        with self.assertRaises(ValueError): d.metadata(info, 'unknown')

    def test_unexpected_zip_paths_are_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            for name in ('../escape', '/absolute', 'Other.app/Contents/file'):
                path, _ = self.archive(root)
                with zipfile.ZipFile(path, 'a') as archive: archive.writestr(name, 'synthetic')
                with self.assertRaises(ValueError): d.validate_zip(path)
                self.assertFalse((root / 'escape').exists())

    def test_modified_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'OUTPUT', pathlib.Path(folder)):
            archive, value = self.archive(pathlib.Path(folder))
            manifest = d.record(value, [archive])
            archive.write_bytes(b'changed artifact')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                d.verify(manifest)

    def test_manifest_cannot_refer_to_files_outside_distribution(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'OUTPUT', pathlib.Path(folder)):
            archive, value = self.archive(pathlib.Path(folder))
            manifest = d.record(value, [archive]);data = json.loads(manifest.read_text())
            data['files'][0]['name'] = '../escape.zip';manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'filename'):
                d.verify(manifest)

    def test_ci_requires_both_formats(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'OUTPUT', pathlib.Path(folder)):
            archive, value = self.archive(pathlib.Path(folder))
            manifest = d.record(value, [archive])
            with patch.object(d, 'run'), patch.object(d, 'validate_app'):
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    d.verify(manifest, require_dmg=True)

    def test_verification_failure_still_detaches_only_its_own_image(self):
        calls = []
        def run(args):
            calls.append([str(v) for v in args])
            if args[1] == 'attach':
                mount = str(args[args.index('-mountpoint') + 1])
                return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [{'mount-point': mount, 'dev-entry': '/dev/synthetic99'}]}))
            return SimpleNamespace(stdout=b'')
        with patch.object(d, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                with d.mounted_image(pathlib.Path('synthetic.dmg')):
                    raise RuntimeError('synthetic failure')
        self.assertEqual(calls[-1], ['/usr/bin/hdiutil', 'detach', '/dev/synthetic99'])
        self.assertIn('-readonly', calls[0]);self.assertIn('-noautoopen', calls[0])

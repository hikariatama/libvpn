import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import fixtures
import runtime_support

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Tools'))
import fetch_python


class PrivateRuntimeTests(unittest.TestCase):
    def test_entry_points_are_closed_and_use_isolation(self):
        root = pathlib.Path('/Synthetic Folder/libvpn')
        for role in ('bridge', 'engine', 'self-test', 'update'):
            args = runtime_support.command(root, role)
            self.assertEqual(args, [str(root / 'Python/bin/python3'), '-I', '-S', '-B', str(root / 'launcher.py'), role])
        with self.assertRaises(ValueError): runtime_support.command(root, '../other')

    def test_macos_trust_uses_os_ca_bundle_with_verification(self):
        with patch.object(runtime_support.sys, 'platform', 'darwin'), patch.object(runtime_support.ssl, 'create_default_context') as create:
            runtime_support.tls_context()
        create.assert_called_once_with(cafile='/etc/ssl/cert.pem')

    def test_python_archive_rejects_paths_and_links_outside_runtime(self):
        for name, target in (('../escape', None), ('/absolute', None), ('python/bin/python3', '../../../outside'), ('python/link', '/absolute')):
            member = tarfile.TarInfo(name)
            if target is not None: member.type = tarfile.SYMTYPE;member.linkname = target
            with self.assertRaises(ValueError): fetch_python.safe_member(member)
        member = tarfile.TarInfo('python/bin/python3');member.type = tarfile.SYMTYPE;member.linkname = 'python3.14'
        fetch_python.safe_member(member)

    def test_embedded_runtime_relocates_and_ignores_user_python_settings(self):
        resources = pathlib.Path(__file__).resolve().parents[1] / 'outputs/libvpn.app/Contents/Resources'
        if not (resources / 'Python/bin/python3').is_file():
            self.skipTest('Build the bundled runtime first')
        with tempfile.TemporaryDirectory(prefix="libvpn runtime's ") as folder:
            root = pathlib.Path(folder)
            relocated = root / 'Relocated Resources'
            shutil.copytree(resources / 'Python', relocated / 'Python')
            for module in resources.glob('*.py'): shutil.copy2(module, relocated / module.name)
            if (resources / 'localizations.json').is_file(): shutil.copy2(resources / 'localizations.json', relocated / 'localizations.json')
            marker = root / 'injected'
            poison = root / 'sitecustomize.py'
            poison.write_text('open(' + repr(str(marker)) + ', "w").write("injected")\n')
            env = {'PATH': '/usr/bin:/bin', 'HOME': folder, 'PYTHONHOME': folder, 'PYTHONPATH': folder,
                   'PYTHONSTARTUP': str(poison), 'SSL_CERT_FILE': str(poison), 'SSL_CERT_DIR': folder}
            result = subprocess.run(runtime_support.command(relocated, 'self-test'), cwd=folder, env=env,
                                    capture_output=True, text=True, check=True, timeout=20)
            self.assertEqual(json.loads(result.stdout), {'ok': True, 'pythonVersion': fetch_python.VERSION, 'isolated': True})
            self.assertFalse(marker.exists())
            self.assertFalse(list(relocated.rglob('__pycache__')))
            invalid = runtime_support.command(relocated, 'self-test');invalid[-1] = '../engine'
            rejected = subprocess.run(invalid, cwd=folder, env=env,
                                      capture_output=True, text=True, timeout=20)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(rejected.stdout, '')

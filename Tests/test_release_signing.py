import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Tools'))
import helper_package
import release_package


def workflow_jobs():
    source = (ROOT / '.github/workflows/signed-release.yml').read_text()
    pieces = re.split(r'^  ([a-z]+):\n', source, flags=re.M)
    return dict(zip(pieces[1::2], pieces[2::2]))


def signing_program():
    source = workflow_jobs()['sign']
    return textwrap.dedent(source.split('# BEGIN TRUSTED MANIFEST SIGNING\n', 1)[1]
                           .split('          # END TRUSTED MANIFEST SIGNING', 1)[0])


class ReleaseBoundaryTests(unittest.TestCase):
    def test_production_secret_has_an_independent_runner_without_candidate_execution(self):
        jobs = workflow_jobs()
        self.assertIn('needs: [resolve, build]', jobs['sign'])
        self.assertIn('needs: [resolve, sign]', jobs['package'])
        self.assertIn('needs: [resolve, package]', jobs['draft'])
        self.assertIn('environment: release-signing', jobs['sign'])
        self.assertNotIn('actions/checkout@', jobs['sign'])
        self.assertNotIn('./build.sh', jobs['sign'])
        self.assertNotIn('./test.sh', jobs['sign'])
        self.assertNotIn('release-tools/', jobs['sign'])
        for name, source in jobs.items():
            if name != 'sign':
                self.assertNotIn('FENNEC_PROTECTED_UPDATE_KEY', source)
                self.assertNotIn('environment: release-signing', source)
                self.assertNotIn('FENNEC_SIGNING_KEY_FILE', source)
        self.assertIn('./build.sh --unsigned --app-only', jobs['build'])
        self.assertIn('release_package.py finalize', jobs['package'])
        self.assertNotIn('release-build-', jobs['sign'])
        for action in re.findall(r'uses: (\S+)', '\n'.join(jobs.values())):
            self.assertRegex(action, r'@[0-9a-f]{40}$')

    def test_signer_and_installed_public_anchor_are_pinned_independently_of_candidate(self):
        source = workflow_jobs()['sign']
        self.assertIn('/927a9113af69516309493118180aad690768504b', source)
        for relative in ('Tools/PackageSignature.swift', 'Resources/update-public-key.txt'):
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertIn(digest, source)
        self.assertLess(source.index('shasum -a 256 -c'), source.index('xcrun swiftc'))
        self.assertLess(source.index('xcrun swiftc'), source.index('secrets.FENNEC_PROTECTED_UPDATE_KEY'))
        compile(signing_program(), '<trusted workflow signer>', 'exec')


@unittest.skipUnless(sys.platform == 'darwin', 'Synthetic signatures require native CryptoKit')
class DetachedReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory()
        cls.native = pathlib.Path(cls.tools.name) / 'PackageSignature'
        subprocess.run(['xcrun', 'swiftc', '-O', '-parse-as-library', str(ROOT / 'Tools/PackageSignature.swift'),
                        '-o', str(cls.native)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tools.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        shutil.copyfile(self.native, self.root / 'PackageSignature')
        (self.root / 'PackageSignature').chmod(0o755)
        private = self.root / 'synthetic.key'
        subprocess.run([str(self.native), 'generate', str(private), str(self.root / 'signing-public-key.txt')],
                       check=True, capture_output=True)
        self.encoded_key = base64.b64encode(private.read_bytes()).decode()
        private.unlink()
        self.manifests = {}
        self.resources = {}
        for architecture in ('arm64', 'x86_64'):
            resources = self.root / ('resources-' + architecture); resources.mkdir()
            release = dict(build='a' * 64, sequence=11, architecture=architecture)
            (resources / 'update-release.json').write_text(json.dumps(release))
            (resources / 'launcher.py').write_text('# synthetic helper only\n')
            shutil.copyfile(self.native, resources / 'PackageSignature')
            (resources / 'PackageSignature').chmod(0o755)
            shutil.copyfile(self.root / 'signing-public-key.txt', resources / 'update-public-key.txt')
            helper_package.pack(resources, resources / 'HelperInstall.zip')
            manifest = helper_package.manifest_for(resources, resources / 'HelperInstall.zip')
            path = self.root / 'release-input' / architecture / 'manifest.json'
            path.parent.mkdir(parents=True); path.write_bytes(helper_package.manifest_bytes(manifest))
            self.manifests[architecture] = path
            self.resources[architecture] = resources

    def sign(self, key=None):
        environment = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'RUNNER_TEMP': str(self.root),
                       'RELEASE_KEY': key if key is not None else self.encoded_key}
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', signing_program()],
                                cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertFalse((self.root / 'fennec-release.key').exists())
        self.assertNotIn(self.encoded_key, result.stdout + result.stderr)
        return result

    def test_detached_signature_preserves_exact_archive_manifest_and_sequence(self):
        # Extra candidate files are inert data, never imports or executable hooks.
        sentinel = self.root / 'executed'
        (self.manifests['arm64'].parent / 'hook.py').write_text(
            'import pathlib\npathlib.Path(' + repr(str(sentinel)) + ').write_text("executed")\n')
        result = self.sign()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        for architecture, resources in self.resources.items():
            signed = self.root / 'signatures' / architecture
            original = (resources / 'HelperInstall.zip').read_bytes()
            release_package.attach(resources, signed)
            package = resources / 'HelperUpdate'
            self.assertEqual((package / 'helper.zip').read_bytes(), original)
            self.assertEqual((package / 'manifest.json').read_bytes(), self.manifests[architecture].read_bytes())
            self.assertEqual(json.loads((package / 'manifest.json').read_bytes())['sequence'], 11)
            self.assertEqual(set(package.iterdir()), {package / name for name in ('helper.zip', 'manifest.json', 'signature.txt')})

    def test_invalid_manifests_are_rejected_before_signing(self):
        path = self.manifests['x86_64']
        original = path.read_bytes()
        value = json.loads(original)
        for change in ({'architecture': 'arm64'}, {'sequence': True}, {'schemaVersion': True},
                       {'size': 128 * 1024 * 1024 + 1}, {'sha256': '../command'}, {'extra': 'field'}):
            with self.subTest(change=change):
                shutil.rmtree(self.root / 'signatures', ignore_errors=True)
                path.write_bytes(helper_package.manifest_bytes(dict(value, **change)))
                result = self.sign()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(list(self.root.glob('signatures/*/signature.txt')))
        for raw in (original + b' ', b'x' * 4096, b'[]'):
            shutil.rmtree(self.root / 'signatures', ignore_errors=True)
            path.write_bytes(raw)
            self.assertNotEqual(self.sign().returncode, 0)
            self.assertFalse(list(self.root.glob('signatures/*/signature.txt')))

    def test_signing_failure_removes_key_and_does_not_accept_a_new_trust_anchor(self):
        result = self.sign(base64.b64encode(b'0' * 32).decode())
        self.assertNotEqual(result.returncode, 0)

    def test_finalize_rejects_mutated_archive_release_manifest_or_signature(self):
        self.assertEqual(self.sign().returncode, 0)
        signed = self.root / 'signatures/arm64'
        resources = self.resources['arm64']
        payload = resources / 'HelperInstall.zip'
        original_payload = payload.read_bytes()
        payload.write_bytes(original_payload + b'changed')
        with patch.object(release_package.d, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'exact built helper'):
                release_package.attach(resources, signed)
            run.assert_not_called()
        payload.write_bytes(original_payload)
        release = resources / 'update-release.json'
        original_release = release.read_bytes()
        value = json.loads(original_release); value['sequence'] += 1
        release.write_text(json.dumps(value))
        with self.assertRaises(ValueError): release_package.attach(resources, signed)
        release.write_bytes(original_release)
        manifest = signed / 'manifest.json'
        original_manifest = manifest.read_bytes()
        manifest.write_bytes(original_manifest + b' ')
        with self.assertRaises(ValueError): release_package.attach(resources, signed)
        manifest.write_bytes(original_manifest)
        signature = signed / 'signature.txt'
        original_signature = signature.read_bytes()
        signature.write_text(base64.b64encode(b'0' * 64).decode())
        with self.assertRaises(subprocess.CalledProcessError): release_package.attach(resources, signed)
        self.assertFalse((resources / 'HelperUpdate').exists())
        signature.write_bytes(original_signature)
        release_package.attach(resources, signed)

    def test_prepare_uses_existing_installation_payload_without_reading_any_key(self):
        app = self.root / 'app'; (app / 'Contents').mkdir(parents=True)
        resources = app / 'Contents/Resources'
        shutil.copytree(self.resources['arm64'], resources)
        output = self.root / 'prepared'
        with patch.object(release_package.d, 'validate_app'), patch.object(release_package.d, 'metadata', return_value={}):
            with patch.dict(os.environ, {'FENNEC_SIGNING_KEY_FILE': '/must-not-read/release.key'}):
                release_package.prepare(app, output)
        self.assertEqual((output / 'manifest.json').read_bytes(), self.manifests['arm64'].read_bytes())
        self.assertEqual(list(output.iterdir()), [output / 'manifest.json'])

    def test_final_packaging_keeps_the_signed_payload_without_rebuilding_helper(self):
        import zipfile
        self.assertEqual(self.sign().returncode, 0)
        architecture = release_package.d.metadata()['architecture']
        expected = release_package.d.metadata()
        app = self.root / 'unsigned/libvpn.app'
        (app / 'Contents').mkdir(parents=True)
        shutil.copyfile(ROOT / 'Resources/Info.plist', app / 'Contents/Info.plist')
        shutil.copytree(self.resources[architecture], app / 'Contents/Resources')
        archive = self.root / (expected['stem'] + '.zip')
        def pack(source, destination):
            with zipfile.ZipFile(destination, 'w') as output:
                for path in source.rglob('*'):
                    if path.is_file(): output.write(path, str(path.relative_to(source.parent)))
        pack(app, archive)
        original_run = release_package.d.run
        commands = []
        def run(args):
            commands.append([str(arg) for arg in args])
            if str(args[0]).endswith('/PackageSignature'):
                return original_run(args)  # Real verification with a disposable synthetic key.
            if args[:4] == ['/usr/bin/ditto', '-x', '-k', archive]:
                with zipfile.ZipFile(archive) as source:
                    source.extractall(args[-1])
                    for item in source.infolist():
                        (args[-1] / item.filename).chmod((item.external_attr >> 16) & 0o777)
            elif args[:3] == ['/usr/bin/ditto', '-c', '-k']:
                pack(args[-2], args[-1])
        output = self.root / 'final'
        with patch.object(release_package.d, 'OUTPUT', output), \
             patch.object(release_package.d, 'ARTWORK', self.root / 'artwork'), \
             patch.object(release_package.d, 'validate_app'), patch.object(release_package.d, 'run', side_effect=run), \
             patch.object(release_package, 'subprocess', Mock()) as packaging:
            release_package.finalize(archive, self.root / 'signatures' / architecture)
        destination = output / archive.name
        packaging.run.assert_called_once_with([str(ROOT / 'Tools/package.sh'), str(destination)], check=True)
        with zipfile.ZipFile(destination) as final:
            prefix = 'libvpn.app/Contents/Resources/'
            self.assertEqual(final.read(prefix + 'HelperUpdate/helper.zip'), final.read(prefix + 'HelperInstall.zip'))
            self.assertEqual(final.read(prefix + 'HelperUpdate/manifest.json'), self.manifests[architecture].read_bytes())
        self.assertFalse(any(command[0] in ('./build.sh', './test.sh') for command in commands))

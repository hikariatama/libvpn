import base64
import copy
import hashlib
import io
import json
import os
import pathlib
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import engine
import updates
import protocol
from configuration import ConfigurationError


@unittest.skipUnless(sys.platform == 'darwin', 'Native CryptoKit verifier requires macOS')
class SignedUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory();cls.tool = pathlib.Path(cls.tools.name) / 'PackageSignature'
        root = pathlib.Path(__file__).resolve().parents[1]
        subprocess.run(['xcrun', 'swiftc', '-O', '-parse-as-library', str(root / 'Tools/PackageSignature.swift'), '-o', str(cls.tool)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tools.cleanup()

    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name);self.resources = self.root / 'Installed';self.resources.mkdir()
        self.private = self.root / 'synthetic.key'
        self.native('generate', self.private, self.resources / 'update-public-key.txt')
        shutil.copy2(self.tool, self.resources / 'PackageSignature')
        (self.resources / 'update-release.json').write_text(json.dumps(dict(sequence=10, build='0' * 64, architecture=platform.machine())))
        for obj, name, value in [(updates, 'RESOURCES', self.resources), (updates, 'ROOT', self.root / 'Stage'),
                                 (updates, 'STATUS', self.root / 'status.json'), (updates, 'PLIST', self.root / 'update.plist'),
                                 (engine, 'HOME', self.resources), (engine, 'OPERATION_LOCK', self.root / 'operation.lock')]:
            replacement = patch.object(obj, name, value);replacement.start();self.addCleanup(replacement.stop)
        self.commands = []
        def run(args, **kwargs):
            self.commands.append([str(value) for value in args])
            if len(args) > 1 and args[1] == 'verify':
                return subprocess.run([str(value) for value in args], capture_output=True)
            return SimpleNamespace(returncode=0)
        replacement = patch.object(engine, 'run', side_effect=run);replacement.start();self.addCleanup(replacement.stop)

    def native(self, *args):
        subprocess.run([str(self.tool)] + [str(value) for value in args], check=True, capture_output=True)

    def package(self, extra=None, sequence=11, architecture=None, private=None):
        release = dict(sequence=sequence, build='a' * 64, architecture=architecture or platform.machine())
        files = {'update-public-key.txt': (self.resources / 'update-public-key.txt').read_bytes(),
                 'update-release.json': json.dumps(release).encode(), 'helper-build.json': json.dumps({'build': release['build']}).encode(),
                 'Python/bin/python3': b'synthetic executable', 'launcher.py': b'# synthetic launcher',
                 'sing-box': b'synthetic executable', 'PackageSignature': b'synthetic verifier'}
        if extra: files.update(extra)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo(name);info.external_attr = 0o100755 << 16
                archive.writestr(info, data)
        data = stream.getvalue()
        manifest = dict(release, schemaVersion=1, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        path = self.root / 'manifest';path.write_text(json.dumps(manifest))
        signature = self.root / 'signature'
        self.native('sign', private or self.private, path, signature)
        return dict(action='update-helper', manifest=path.read_text(), signature=signature.read_text(), archive=base64.b64encode(data).decode())

    def test_signed_package_is_staged_before_a_fixed_launchd_job_is_started(self):
        reply = updates.prepare(self.package())
        self.assertTrue(reply['updateAccepted'])
        self.assertFalse(updates.PLIST.exists())
        self.assertFalse(any('launchctl' in command[0] for command in self.commands))
        self.assertEqual(updates.ROOT.stat().st_mode & 0o777, 0o700)
        updates.start(501)
        value = plistlib.loads(updates.PLIST.read_bytes())
        self.assertEqual(value['ProgramArguments'], [str(updates.ROOT / 'Resources/Python/bin/python3'), '-I', '-S', '-B',
                                                   str(updates.ROOT / 'Resources/launcher.py'), 'update', '501'])
        self.assertNotIn('KeepAlive', value)

    def test_tampered_signature_manifest_archive_and_wrong_key_never_execute_candidate_code(self):
        for field in ('signature', 'manifest', 'archive', 'key'):
            request = self.package()
            if field == 'signature': request[field] = base64.b64encode(b'0' * 64).decode()
            elif field == 'manifest': request[field] = request[field].replace('"sequence": 11', '"sequence": 999')
            elif field == 'archive': request[field] = base64.b64encode(b'altered').decode()
            else:
                other = self.root / 'other.key';public = self.root / 'other.pub'
                self.native('generate', other, public);request = self.package(private=other)
            with self.assertRaises((ValueError, ConfigurationError)): updates.prepare(request)
            self.assertFalse(updates.PLIST.exists())
            self.assertFalse((updates.ROOT / 'Resources').exists())
        self.assertTrue(all(command[1] == 'verify' for command in self.commands))

    def test_replay_downgrade_wrong_architecture_and_paths_are_rejected(self):
        for request in (self.package(sequence=10), self.package(sequence=9), self.package(architecture='other'),
                        self.package(extra={'../outside': b'bad'}), self.package(extra={'/absolute': b'bad'})):
            with self.assertRaises((ValueError, ConfigurationError)): updates.prepare(request)
        self.assertFalse((self.root / 'outside').exists())
        self.assertFalse(updates.PLIST.exists())

    def test_update_requests_are_bounded_and_never_accept_paths_commands_or_languages(self):
        request = self.package();protocol.validate(request)
        for extra in ({'path': '/tmp/attacker'}, {'command': 'shell'}, {'language': 'unknown'}):
            with self.assertRaises(ConfigurationError): protocol.validate(dict(request, **extra))
        with patch.object(updates, 'MAX_ARCHIVE', 8):
            with self.assertRaises(ConfigurationError): updates.prepare(request)

    def test_overlapping_update_and_symlink_directory_are_rejected(self):
        updates.ROOT.symlink_to(self.resources, target_is_directory=True)
        with self.assertRaises(ConfigurationError): updates.prepare(self.package())
        updates.ROOT.unlink()
        engine.atomic_json(updates.STATUS, {'state': 'updating', 'started': __import__('time').time()})
        with self.assertRaises(ConfigurationError): updates.prepare(self.package())

    def test_worker_records_failure_and_removes_boot_job_without_leaking_exception(self):
        updates.PLIST.write_text('synthetic plist')
        with patch('installation.execute', side_effect=RuntimeError('PRIVATE-UUID')), patch.object(os, 'geteuid', return_value=0):
            updates.run(501)
        self.assertEqual(updates.state()['state'], 'failed')
        self.assertNotIn('PRIVATE-UUID', updates.STATUS.read_text())
        self.assertFalse(updates.PLIST.exists())

    def test_obsolete_boot_job_cannot_downgrade_a_newer_manual_installation(self):
        staged = self.root / 'Older';staged.mkdir()
        (staged / 'update-release.json').write_text(json.dumps(dict(sequence=9, build='b' * 64, architecture=platform.machine())))
        shutil.copy2(self.resources / 'update-public-key.txt', staged / 'update-public-key.txt')
        with patch.object(updates, 'RESOURCES', staged), patch.object(os, 'geteuid', return_value=0), patch('installation.execute') as install:
            updates.run(501)
        install.assert_not_called()
        self.assertEqual(updates.state()['state'], 'failed')

    def test_stalled_update_is_reported_as_failure_instead_of_polling_forever(self):
        engine.atomic_json(updates.STATUS, {'state': 'updating', 'started': 0, 'secret': 'PRIVATE-UUID'})
        self.assertEqual(updates.state()['state'], 'failed')
        self.assertNotIn('PRIVATE-UUID', str(updates.state()))

"""Exercise real detectors using generated, nonfunctional credential fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


class SecretScanningTests(unittest.TestCase):
    def scan(self, files):
        binary = os.environ.get('GITLEAKS_BINARY') or shutil.which('gitleaks')
        if not binary:
            self.skipTest('Install the pinned Gitleaks binary first')
        with tempfile.TemporaryDirectory(prefix='gitleaks-test.', dir=ROOT / 'work') as folder:
            root = Path(folder);source = root / 'source';source.mkdir()
            for name, text in files.items():
                path = source / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(text)
            report = root / 'report.json';ignore = root / 'empty-ignore';ignore.touch()
            result = subprocess.run([binary, 'dir', str(source), '--config', str(ROOT / '.gitleaks.toml'),
                '--gitleaks-ignore-path', str(ignore), '--ignore-gitleaks-allow', '--redact=100', '--no-banner', '--no-color',
                '--report-format', 'json', '--report-path', str(report)], capture_output=True, text=True)
            if result.returncode not in (0, 1):
                self.fail('Gitleaks could not execute the synthetic scan')
            findings = json.loads(report.read_text())
            self.assertTrue(all(item['Secret'] == 'REDACTED' for item in findings))
            return result.returncode, {item['RuleID'] for item in findings}

    def test_default_detector_catches_a_generated_fake_github_token_even_in_tests(self):
        token = 'ghp_' + hashlib.sha256(b'nonfunctional test token').hexdigest()[:36]
        code, rules = self.scan({'Tests/fixture.py': 'token = ' + repr(token)})
        self.assertEqual(code, 1)
        self.assertIn('github-pat', rules)

    def test_vless_credentials_are_detected_in_sources_and_tests(self):
        identity = str(uuid.UUID(bytes=hashlib.sha256(b'synthetic VPN credential').digest()[:16], version=4))
        value = 'vless:' + '//' + identity + '@example.invalid:443'
        for name in ('Source/example.txt', 'Tests/example.txt'):
            with self.subTest(path=name):
                code, rules = self.scan({name: value})
                self.assertEqual(code, 1)
                self.assertIn('vless-credential', rules)

    def test_only_the_known_test_identity_is_allowed_in_tests(self):
        value = 'vless:' + '//00000000-0000-4000-8000-000000000001@example.invalid:443'
        self.assertEqual(self.scan({'Tests/fixture.txt': value})[0], 0)
        self.assertEqual(self.scan({'Source/fixture.txt': value})[0], 1)

    def test_inline_suppression_cannot_disable_the_scan(self):
        token = 'ghp_' + hashlib.sha256(b'another nonfunctional test token').hexdigest()[:36]
        self.assertEqual(self.scan({'example.txt': token + ' # gitleaks:allow'})[0], 1)

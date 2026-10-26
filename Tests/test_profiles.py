import base64
import copy
import hashlib
import io
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch
from fixtures import config
from configuration import normalize, empty_configuration, public, ConfigurationError
import bridge
import engine
import profiles
import protocol


class ProfileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        base = patch.object(engine, 'BASE', pathlib.Path(folder.name)); base.start(); self.addCleanup(base.stop)

    def example(self, root):
        value = config();value['revision'] = 12
        blob = b'synthetic binary rules' * 50000
        digest = hashlib.sha256(blob).hexdigest()
        (root / (digest + '.srs')).write_bytes(blob)
        value['groups'] = [{'id': 'custom', 'title': 'Custom', 'flag': '', 'nodes': ['sample'],
                            'enabled': True, 'domains': ['example.invalid'], 'snapshots': [digest]}]
        return value

    def test_empty_and_full_profiles_round_trip_credentials_order_and_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            for value in (empty_configuration(), self.example(root)):
                encoded = profiles.pack(value, root)
                imported, blobs, digest = profiles.unpack(encoded, 21)
                self.assertEqual(imported['revision'], 21)
                self.assertEqual(imported['fallback'], normalize(value)['fallback'])
                self.assertEqual([n['uuid'] for n in imported['nodes']], [n['uuid'] for n in value['nodes']])
                self.assertTrue(all(not n['resolved'] for n in imported['nodes']))
                self.assertEqual(set(blobs), profiles.references(imported))
                self.assertEqual(digest, hashlib.sha256(base64.b64decode(encoded)).hexdigest())
                self.assertNotIn('uuid', json.dumps(public(imported)))

    def test_unexpected_paths_duplicates_corrupt_snapshots_and_zip_bombs_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder);value = self.example(root)
            for failure in ('path', 'digest', 'duplicate', 'size'):
                data = io.BytesIO(base64.b64decode(profiles.pack(value, root)))
                with zipfile.ZipFile(data, 'a') as archive:
                    if failure == 'path': archive.writestr('../outside', b'data')
                    if failure == 'digest':
                        name = next(v for v in archive.namelist() if v.startswith('assets/'))
                        archive.writestr(name, b'changed')
                    if failure == 'duplicate': archive.writestr('configuration.json', b'{}')
                context = patch.object(profiles, 'MAX_CONTENT', 8) if failure == 'size' else __import__('contextlib').nullcontext()
                with context, self.assertRaises(ConfigurationError): profiles.unpack(base64.b64encode(data.getvalue()).decode(), 1)
                self.assertFalse((root.parent / 'outside').exists())

    def test_preview_is_safe_and_stale_confirmation_never_applies(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder);value = self.example(root)
            encoded = profiles.pack(value, root)
            with patch.object(engine, 'load_configuration', return_value=value), patch.object(bridge, 'transaction') as tx:
                result = bridge.dispatch({'action': 'preview-profile', 'revision': 12, 'archive': encoded})
                tx.assert_not_called()
                self.assertEqual(result['profile']['servers'], 1)
                self.assertNotIn('00000000-', json.dumps(result));self.assertNotIn('192.0.2.1', json.dumps(result))
                with self.assertRaises(ConfigurationError): bridge.dispatch({'action': 'import-profile', 'revision': 11, 'archive': encoded, 'digest': result['profile']['digest']})
                tx.assert_not_called()

    def test_failed_apply_cleans_new_assets_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder);source = root / 'source';source.mkdir()
            encoded = profiles.pack(self.example(source), source)
            value = empty_configuration();assets = root / 'assets'
            _, _, digest = profiles.unpack(encoded, 1)
            tx = Mock();tx.apply.side_effect = ConfigurationError('synthetic validation failure')
            with patch.object(engine, 'load_configuration', return_value=value), patch.object(engine, 'ASSETS', assets), patch.object(engine, 'BASE', root), patch.object(engine, 'resolve_nodes'), patch.object(bridge, 'transaction', return_value=tx):
                with self.assertRaises(ConfigurationError): bridge.dispatch({'action': 'import-profile', 'revision': 0, 'archive': encoded, 'digest': digest})
            self.assertFalse(list(assets.iterdir()))
            self.assertEqual(value, empty_configuration())

    def test_export_is_explicit_and_protocol_keeps_ordinary_request_limits(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'ASSETS', pathlib.Path(folder)), patch.object(engine, 'load_configuration', return_value=empty_configuration()), patch.object(engine, 'read_state', return_value={}):
            self.assertNotIn('archive', bridge.dispatch({'action': 'status'}))
            self.assertIn('archive', bridge.dispatch({'action': 'export-profile'}))
        with patch.object(protocol, 'MAX_BYTES', 10):
            with self.assertRaises(ConfigurationError): protocol.validate({'action': 'status'})

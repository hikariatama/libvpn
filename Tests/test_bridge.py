import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from fixtures import config
import bridge
import engine
import protocol
from configuration import ConfigurationError
from store import RecoveryError, Transaction


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        base = patch.object(engine, 'BASE', pathlib.Path(folder.name)); base.start(); self.addCleanup(base.stop)
        status = patch.object(bridge.updates, 'STATUS', pathlib.Path(folder.name) / 'update-status.json'); status.start(); self.addCleanup(status.stop)

    def test_helper_readiness_waits_for_the_expected_build(self):
        replies = [{'ok': True, 'version': 9}, {'ok': True, 'installed': True, 'version': 9, 'helperBuild': 'previous'},
                   {'ok': True, 'installed': True, 'version': 9, 'helperBuild': 'expected'}]
        with patch.object(bridge, 'client', side_effect=replies) as request, patch.object(bridge.time, 'sleep'):
            bridge.ready_helper('expected')
        self.assertEqual(request.call_count, 3)

    def test_fragmented_frame_and_duplicate_keys(self):
        chunks = iter([b'{"action":', b'"status"}\n'])
        self.assertEqual(protocol.read_frame(lambda _: next(chunks))['action'], 'status')
        for data in (b'{"action":"status","action":"traffic"}\n', b'[]\nextra', b'{}', b'NaN\n'):
            parts = iter([data, b''])
            with self.assertRaises(ConfigurationError): protocol.read_frame(lambda _: next(parts))

    def test_oversized_and_unexpected_requests(self):
        with self.assertRaises(ConfigurationError): protocol.read_frame(lambda _: b'x' * 10, 8)
        for request in ({'action': 'shell'}, {'action': 'status', 'path': '/tmp/x'}, {'action': 'set-kill-switch', 'revision': 0, 'enabled': 1}):
            with self.assertRaises(ConfigurationError): protocol.validate(request)

    def test_status_has_no_private_endpoint_or_secret(self):
        with patch.object(engine, 'load_configuration', return_value=config()), patch.object(engine, 'read_state', return_value={'state': 'on'}):
            value = bridge.dispatch({'action': 'status'})
        self.assertNotIn('192.0.2.1', json.dumps(value))
        self.assertNotIn('00000000-0000', json.dumps(value))

    def test_transient_status_failure_does_not_request_helper_reinstallation(self):
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = protocol.encode({'ok': False, 'error': 'Другое изменение ещё выполняется'})
        with patch.object(bridge.socket, 'socket', return_value=connection):
            result = bridge.client({'action': 'status'})
        self.assertTrue(result['installed'])
        self.assertFalse(result['ok'])
        self.assertFalse(result.get('requiresUpdate', False))

    def test_legacy_status_handshake_retries_without_locale_only_once(self):
        from unittest.mock import MagicMock
        connection = MagicMock();connection.__enter__.return_value = connection
        connection.recv.side_effect = [protocol.encode({'ok': False, 'error': 'Unknown field'}),
                                       protocol.encode({'ok': True, 'version': 9, 'helperBuild': 'legacy'})]
        with patch.object(bridge.socket, 'socket', return_value=connection):
            result = bridge.client({'action': 'status', 'language': 'en'})
        requests = [json.loads(call.args[0]) for call in connection.sendall.call_args_list]
        self.assertEqual(requests, [{'action': 'status', 'language': 'en'}, {'action': 'status'}])
        self.assertEqual(result['helperBuild'], 'legacy')

    def test_untrusted_peer_is_rejected_before_request_is_read(self):
        from unittest.mock import Mock
        connection = Mock()
        with patch.object(bridge, 'peer_uid', return_value=999): bridge.handle(connection, 501)
        connection.recv.assert_not_called()
        self.assertFalse(json.loads(connection.sendall.call_args[0][0])['ok'])

    def test_internal_errors_never_echo_payloads(self):
        from unittest.mock import Mock
        connection = Mock();connection.recv.return_value = b'{"action":"status"}\n'
        with patch.object(bridge, 'peer_uid', return_value=501), patch.object(bridge, 'dispatch', side_effect=RuntimeError('SECRET-UUID')):
            bridge.handle(connection, 501)
        self.assertNotIn('SECRET-UUID', str(connection.sendall.call_args))


class TransactionTests(unittest.TestCase):
    def execute(self, fail=None, rollback_failure=False):
        old = config();old['killSwitch'] = True
        new = copy.deepcopy(old);new['revision'] = 1;new['nodes'][0]['address'] = '198.51.100.2'
        events = []
        def check(value):
            events.append('check')
            if fail == 'check': raise RuntimeError()
        def stop(): events.append('stop')
        def guard(value): events.append(('guard', value['killSwitch']))
        def start(): events.append('start')
        def ready(revision):
            events.append(('ready', revision))
            if fail == 'ready' and (revision == 1 or rollback_failure): raise RuntimeError()
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'BASE', pathlib.Path(folder)), patch.object(engine, 'CONFIG', pathlib.Path(folder) / 'configuration.json'), patch.object(engine, 'OPERATION_LOCK', pathlib.Path(folder) / 'operations.lock'):
            engine.atomic_json(engine.CONFIG, old)
            tx = Transaction(check, stop, guard, start, ready)
            if fail:
                with self.assertRaises((RuntimeError, ConfigurationError)): tx.apply(old, new)
            else: tx.apply(old, new)
            saved = json.loads(engine.CONFIG.read_text())
            journal = (engine.BASE / 'transaction.json').exists()
        return events, saved, journal

    def test_overlapping_process_operations_are_rejected_before_services_change(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'BASE', pathlib.Path(folder)), patch.object(engine, 'CONFIG', pathlib.Path(folder)/'configuration.json'), patch.object(engine, 'OPERATION_LOCK', pathlib.Path(folder)/'operations.lock'):
            old=config(); engine.atomic_json(engine.CONFIG,old)
            callbacks=[Mock() for _ in range(5)]
            tx=Transaction(*callbacks)
            with engine.configuration_lock():
                with self.assertRaises(ConfigurationError): tx.apply(old,dict(old,revision=1))
            for callback in callbacks: callback.assert_not_called()
            engine.atomic_json(engine.CONFIG,dict(old,revision=2))
            with self.assertRaises(ConfigurationError): tx.apply(old,dict(old,revision=1))
            for callback in callbacks: callback.assert_not_called()

    def test_incomplete_rollback_cannot_be_overwritten_by_another_operation(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'BASE', pathlib.Path(folder)), patch.object(engine, 'CONFIG', pathlib.Path(folder)/'configuration.json'), patch.object(engine, 'OPERATION_LOCK', pathlib.Path(folder)/'operations.lock'):
            old=config(); engine.atomic_json(engine.CONFIG,old)
            journal=engine.BASE/'transaction.json';engine.atomic_json(journal,{'old':old,'guard':old})
            before=journal.read_bytes();callbacks=[Mock() for _ in range(5)]
            callbacks[4].side_effect=RuntimeError('synthetic readiness failure')
            with self.assertRaises(RecoveryError): Transaction(*callbacks).apply(old,dict(old,revision=1))
            self.assertEqual(journal.read_bytes(),before)
            callbacks[0].assert_not_called()
            self.assertEqual(callbacks[2].call_args[0][0], old)

    def test_candidate_checked_before_any_mutation(self):
        events, saved, journal = self.execute('check')
        self.assertEqual(events, ['check']);self.assertEqual(saved['revision'], 0);self.assertFalse(journal)

    def test_success_and_rollback(self):
        events, saved, journal = self.execute()
        self.assertEqual(events[0:2], ['check', 'stop']);self.assertEqual(saved['revision'], 1);self.assertFalse(journal)
        events, saved, journal = self.execute('ready')
        self.assertIn(('ready', 0), events);self.assertEqual(saved['revision'], 0);self.assertFalse(journal)

    def test_failed_rollback_retains_durable_guard(self):
        events, saved, journal = self.execute('ready', True)
        self.assertTrue(journal);self.assertEqual(events[-1], ('guard', True))

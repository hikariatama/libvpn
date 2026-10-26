import copy
import json
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import bridge
import engine
from configuration import ConfigurationError, normalize
from fixtures import config
from store import RecoveryError, Transaction


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        for name, path in {'BASE': self.root, 'CONFIG': self.root / 'configuration.json',
                           'OPERATION_LOCK': self.root / 'lock'}.items():
            replacement = patch.object(engine, name, path)
            replacement.start(); self.addCleanup(replacement.stop)
        replacement = patch.object(bridge, 'failed_recovery', None)
        replacement.start(); self.addCleanup(replacement.stop)
        self.old = normalize(config())
        self.new = copy.deepcopy(self.old); self.new['revision'] += 1
        self.protection = copy.deepcopy(self.old); self.protection['killSwitch'] = True
        self.journal = self.root / 'transaction.json'
        engine.atomic_json(engine.CONFIG, self.new)
        engine.atomic_json(self.journal, {'old': self.old, 'guard': self.protection})
        self.events = []
        self.tx = Transaction(lambda _: self.events.append('check'),
                              lambda: self.events.append('stop'),
                              lambda value: self.events.append(('guard', engine.protected(value))),
                              lambda: self.events.append('start'),
                              lambda revision: self.events.append(('ready', revision)))

    def test_status_recovers_without_reinstall_and_removes_journal_only_after_readiness(self):
        with patch.object(bridge, 'transaction', return_value=self.tx), patch.object(engine, 'read_state', return_value={'state': 'on'}):
            result = bridge.dispatch({'action': 'status'})
            bridge.dispatch({'action': 'status'})
        self.assertFalse(result['recoveryRequired'])
        self.assertEqual(result['configuration']['revision'], self.old['revision'])
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.events, ['stop', ('guard', True), 'start', ('ready', 0), ('guard', False)])
        self.assertNotIn('192.0.2.1', json.dumps(result))

    def test_failed_recovery_preserves_guard_and_is_not_retried_by_polling_or_mutations(self):
        self.tx.ready = Mock(side_effect=RuntimeError('SECRET-ENDPOINT'))
        original = self.journal.read_bytes()
        with patch.object(bridge, 'transaction', return_value=self.tx), patch.object(engine, 'read_state', return_value={'state': 'on'}):
            for _ in range(3):
                result = bridge.dispatch({'action': 'status'})
                self.assertTrue(result['recoveryRequired'])
                self.assertEqual(result['state'], 'blocked')
                self.assertNotIn('SECRET-ENDPOINT', json.dumps(result))
            with self.assertRaises(ConfigurationError):
                bridge.dispatch({'action': 'set-bypass', 'revision': 0, 'enabled': True})
        self.tx.ready.assert_called_once_with(0)
        self.assertEqual(self.events[-1], ('guard', True))
        self.assertEqual(self.journal.read_bytes(), original)

    def test_explicit_reconnect_can_retry_a_failed_recovery(self):
        self.tx.ready = Mock(side_effect=RuntimeError())
        with patch.object(bridge, 'transaction', return_value=self.tx):
            self.assertIsNotNone(bridge.recover_if_needed())
            self.tx.ready = Mock()
            self.assertIsNone(bridge.recover_if_needed(retry=True))
        self.assertFalse(self.journal.exists())

    def test_active_installation_lock_is_transient_and_never_latches_recovery_failure(self):
        with patch.object(bridge, 'transaction', return_value=self.tx), engine.configuration_lock():
            with self.assertRaises(ConfigurationError): bridge.recover_if_needed()
        self.assertIsNone(bridge.failed_recovery)
        self.assertEqual(self.events, [])
        with patch.object(bridge, 'transaction', return_value=self.tx):
            self.assertIsNone(bridge.recover_if_needed())

    def test_mutation_resumes_after_recovery_with_revision_validation(self):
        self.tx.apply(self.old, self.new)
        self.assertFalse(self.journal.exists())
        self.assertEqual(json.loads(engine.CONFIG.read_text())['revision'], 1)
        self.assertLess(self.events.index(('ready', 0)), self.events.index('check'))
        self.assertEqual(self.events[-1], ('guard', False))

    def test_corrupt_journal_is_retained_without_mutating_services(self):
        self.journal.write_text('{broken')
        with self.assertRaises(RecoveryError): self.tx.recover()
        self.assertEqual(self.events, [])
        self.assertTrue(self.journal.exists())

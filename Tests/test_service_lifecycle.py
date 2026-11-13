"""Simulate asynchronous launchd removal without touching installed services."""
import copy
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fixtures import config
import bridge
import engine
from configuration import ConfigurationError
from store import Transaction


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        for key, name in (('BASE', ''), ('CONFIG', 'configuration.json'),
                          ('STATE', 'state.json'), ('OPERATION_LOCK', 'lock')):
            change = patch.object(engine, key, self.root / name)
            change.start(); self.addCleanup(change.stop)
        self.clock = 0.0
        change = patch.object(bridge.time, 'monotonic', side_effect=lambda: self.clock)
        change.start(); self.addCleanup(change.stop)
        change = patch.object(bridge.time, 'sleep', side_effect=self.advance)
        change.start(); self.addCleanup(change.stop)

    def advance(self, seconds):
        self.clock += seconds

    def test_change_waits_for_old_job_and_retries_transient_registration(self):
        old = config(); new = copy.deepcopy(old); new['revision'] += 1
        engine.atomic_json(engine.CONFIG, old)
        results = [('bootout', 0), ('print', 0), ('print', 0), ('print', 113),
                   ('bootstrap', 5), ('bootstrap', 37), ('bootstrap', 0)]
        calls = []
        def launch(args, **kwargs):
            expected, code = results.pop(0)
            self.assertEqual(args[:2], ['/bin/launchctl', expected])
            calls.append(expected)
            return SimpleNamespace(returncode=code, stderr='SECRET-ENDPOINT')
        ready = Mock(); guard = Mock(); report = Mock()
        with patch.object(engine, 'run', side_effect=launch):
            Transaction(Mock(), bridge.stop_engine, guard, bridge.start_engine, ready, report).apply(old, new)
        self.assertEqual(results, [])
        self.assertEqual(calls[:4], ['bootout', 'print', 'print', 'print'])
        ready.assert_called_once_with(new['revision'])
        report.assert_not_called()
        self.assertFalse((self.root / 'transaction.json').exists())
        self.assertEqual(engine.load_configuration()['revision'], new['revision'])

    def test_stop_timeout_never_starts_another_engine(self):
        calls = []
        def launch(args, **kwargs):
            calls.append(args[1]); return SimpleNamespace(returncode=0)
        with patch.object(engine, 'run', side_effect=launch):
            with self.assertRaisesRegex(ConfigurationError, 'остановить'):
                bridge.stop_engine()
        self.assertGreaterEqual(self.clock, 15)
        self.assertNotIn('bootstrap', calls)

    def test_registration_retry_is_bounded_and_never_exposes_stderr(self):
        with patch.object(engine, 'run', return_value=SimpleNamespace(returncode=5, stderr='SECRET-ENDPOINT')) as launch:
            with self.assertRaisesRegex(ConfigurationError, '^Не удалось запустить движок$'):
                bridge.start_engine()
        self.assertGreaterEqual(self.clock, 15)
        self.assertLessEqual(launch.call_count, 151)

    def test_permanent_launch_failure_is_not_retried(self):
        with patch.object(engine, 'run', return_value=SimpleNamespace(returncode=78, stderr='SECRET-ENDPOINT')) as launch:
            with self.assertRaisesRegex(ConfigurationError, '^Не удалось запустить движок$'):
                bridge.start_engine()
        launch.assert_called_once()
        self.assertEqual(self.clock, 0)

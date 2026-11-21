import contextlib
import copy
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from fixtures import config
from configuration import ConfigurationError
import engine


class PFTighteningTests(unittest.TestCase):
    @contextlib.contextmanager
    def environment(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            root = pathlib.Path(folder)
            value = config(); value['groups'][0]['killSwitch'] = True
            closed = engine.build_pf_rules(value)
            live = [engine.build_pf_rules(value, 'utun42')]
            self.assertTrue(live[0].endswith('pass out all\n'))
            anchor = root / 'anchor'; anchor.write_text(closed)
            events = []
            def run(args, input_text=None, **kwargs):
                if args[:3] == ['/sbin/pfctl', '-n', '-a']:
                    events.append('validate-live' if args[-1] == '-' else 'validate-disk')
                elif args[:3] == ['/sbin/pfctl', '-a', engine.PF_ANCHOR]:
                    events.append('load'); live[0] = input_text
                elif args == ['/sbin/pfctl', '-F', 'states']:
                    events.append('flush')
                elif args == ['/usr/sbin/sysctl', '-n', 'kern.boottime']:
                    return SimpleNamespace(returncode=0, stdout='synthetic-boot')
                return SimpleNamespace(returncode=0, stdout='Status: Enabled')
            for name, path in {'PF_FILE': anchor, 'PF_REFERENCE': root / 'reference', 'BASE': root,
                               'PF_LOCK': root / 'lock', 'RUNTIME': root / 'runtime.json'}.items():
                stack.enter_context(patch.object(engine, name, path))
            stack.enter_context(patch.object(engine, 'pf_lock', side_effect=contextlib.nullcontext))
            stack.enter_context(patch.object(engine, 'run', side_effect=run))
            yield root, value, closed, live, events

    @contextlib.contextmanager
    def storage_fault(self, failure):
        with contextlib.ExitStack() as stack:
            error = OSError('synthetic storage failure: ' + failure)
            if failure == 'create':
                stack.enter_context(patch.object(tempfile, 'mkstemp', side_effect=error))
            elif failure in ('write', 'flush'):
                original = os.fdopen
                @contextlib.contextmanager
                def fdopen(*args, **kwargs):
                    with original(*args, **kwargs) as output:
                        proxy = Mock(wraps=output)
                        getattr(proxy, failure).side_effect = error
                        yield proxy
                stack.enter_context(patch.object(engine.os, 'fdopen', side_effect=fdopen))
            elif failure in ('file-fsync', 'directory-fsync'):
                original = os.fsync
                calls = [0]
                def fsync(fd):
                    calls[0] += 1
                    if calls[0] == (1 if failure == 'file-fsync' else 2):
                        raise error
                    original(fd)
                stack.enter_context(patch.object(engine.os, 'fsync', side_effect=fsync))
            elif failure == 'validate':
                original = engine.run
                def run(args, *a, **kwargs):
                    if args[:3] == ['/sbin/pfctl', '-n', '-a'] and args[-1] != '-':
                        return SimpleNamespace(returncode=1)
                    return original(args, *a, **kwargs)
                stack.enter_context(patch.object(engine, 'run', side_effect=run))
            elif failure == 'rename':
                stack.enter_context(patch.object(engine.os, 'replace', side_effect=error))
            yield

    def test_each_persistence_failure_still_closes_live_rules_and_flushes_states(self):
        for failure in ('create', 'write', 'flush', 'file-fsync', 'validate', 'rename', 'directory-fsync'):
            with self.subTest(failure=failure), self.environment() as (root, value, closed, live, events):
                original = copy.deepcopy(value)
                with patch.object(engine, 'ensure_pf_enabled', side_effect=lambda: events.append('enable')), self.storage_fault(failure):
                    with self.assertRaises((OSError, ConfigurationError)):
                        engine.apply_kill_switch(value, flush=True)
                self.assertEqual(live[0], closed)
                self.assertEqual(events.count('flush'), 1)
                self.assertLess(events.index('load'), events.index('flush'))
                self.assertEqual((root / 'anchor').read_text(), closed)
                self.assertEqual(list(root.iterdir()), [root / 'anchor'])
                self.assertEqual(value, original)

    def test_opening_tun_still_requires_durable_closed_disk_guard(self):
        for failure in ('create', 'write', 'flush', 'file-fsync', 'validate', 'rename', 'directory-fsync'):
            with self.subTest(failure=failure), self.environment() as (_, value, closed, live, events):
                live[0] = closed
                with patch.object(engine, 'ensure_pf_enabled') as enabled, self.storage_fault(failure):
                    with self.assertRaises((OSError, ConfigurationError)):
                        engine.apply_kill_switch(value, 'utun42', flush=True)
                self.assertEqual(live[0], closed)
                self.assertNotIn('load', events)
                self.assertNotIn('flush', events)
                enabled.assert_not_called()

    def test_marker_failure_cannot_skip_flush_or_anchor_persistence(self):
        with self.environment() as (root, value, closed, live, events):
            with patch.object(engine, 'atomic_json', side_effect=OSError('synthetic marker failure')):
                with self.assertRaisesRegex(OSError, 'marker failure'):
                    engine.apply_kill_switch(value, flush=True)
            self.assertEqual(live[0], closed)
            self.assertEqual(events.count('flush'), 1)
            self.assertLess(events.index('flush'), events.index('validate-disk'))
            self.assertEqual((root / 'anchor').read_text(), closed)

    def test_kernel_or_enable_failure_does_not_skip_remaining_emergency_attempts(self):
        for failure in ('load', 'enable', 'flush'):
            with self.subTest(failure=failure), self.environment() as (_, value, _, _, events):
                original = engine.run
                def run(args, *a, **kwargs):
                    result = original(args, *a, **kwargs)
                    if failure == 'load' and args[:2] == ['/sbin/pfctl', '-a']:
                        return SimpleNamespace(returncode=1)
                    if failure == 'flush' and args == ['/sbin/pfctl', '-F', 'states']:
                        return SimpleNamespace(returncode=1)
                    return result
                def enable():
                    events.append('enable')
                    if failure == 'enable':
                        raise OSError('synthetic enable failure')
                with patch.object(engine, 'run', side_effect=run), patch.object(engine, 'ensure_pf_enabled', side_effect=enable):
                    with self.assertRaises((OSError, ConfigurationError)):
                        engine.apply_kill_switch(value, flush=True)
                self.assertIn('load', events)
                self.assertIn('enable', events)
                self.assertIn('flush', events)
                self.assertIn('validate-disk', events)

    def test_flush_policy_is_unchanged_for_no_flush_and_bypass(self):
        with self.environment() as (_, value, _, _, events), patch.object(engine, 'ensure_pf_enabled'):
            engine.apply_kill_switch(value)
            self.assertNotIn('flush', events)
            value['bypass'] = True
            engine.apply_kill_switch(value, flush=True)
            self.assertNotIn('flush', events)

    def test_supervisor_child_loss_closes_even_when_cleanup_cannot_persist(self):
        with self.environment() as (root, value, closed, live, events):
            (root / 'token').write_text('synthetic')
            stopping = Mock(); stopping.wait.return_value = False
            child = Mock(); child.poll.side_effect = [None, 1, 1]
            original = engine.write_pf_anchor
            writes = [0]
            def persist(data):
                writes[0] += 1
                if writes[0] == 3:
                    raise OSError('synthetic child-loss disk failure')
                return original(data)
            with patch.object(engine.os, 'geteuid', return_value=0), patch.object(engine.signal, 'signal'), \
                 patch.object(engine.threading, 'Event', return_value=stopping), \
                 patch.object(engine, 'load_configuration', return_value=value), \
                 patch.object(engine, 'TOKEN', root / 'token'), patch.object(engine, 'build_runtime', return_value={}), \
                 patch.object(engine, 'interfaces', return_value=set()), patch.object(engine, 'own_tunnel', return_value='utun42'), \
                 patch.object(engine.subprocess, 'Popen', return_value=child), patch.object(engine, 'publish') as publish, \
                 patch.object(engine, 'ensure_pf_enabled'), patch.object(engine, 'write_pf_anchor', side_effect=persist):
                engine.serve()
            self.assertEqual(writes[0], 3)
            self.assertEqual(live[0], closed)
            self.assertEqual(events.count('flush'), 2)  # Existing startup + child-loss policy.
            self.assertEqual(publish.call_args.args[0], 'error')
            child.terminate.assert_not_called()

import contextlib
import json
import pathlib
import plistlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fixtures import config
import bridge
import engine
import installation


class InstallationTests(unittest.TestCase):
    @contextlib.contextmanager
    def environment(self, fail=False):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            root = pathlib.Path(folder)
            home = root / 'Home';base = home / 'Engine';base.mkdir(parents=True)
            engine_config = base / 'configuration.json';engine_config.write_text(json.dumps(config()))
            marker = home / 'preserved';marker.write_text('previous installation')
            resources = root / 'Resources';resources.mkdir();(resources / 'sing-box').write_text('synthetic executable')
            (resources / 'bridge.py').write_text('# synthetic module')
            (resources / 'launcher.py').write_text('# synthetic launcher')
            python = resources / 'Python/bin/python3';python.parent.mkdir(parents=True)
            python.write_text('synthetic private interpreter');python.chmod(0o755)
            plists = root / 'LaunchDaemons';plists.mkdir()
            for label in installation.LABELS[:2]: (plists / (label + '.plist')).write_text('previous plist')
            pf = root / 'pf.conf';pf.write_text('previous pf')
            anchor = root / 'anchor';anchor.write_text('previous anchor')
            replacements = {'HOME': home, 'BASE': base, 'CONFIG': engine_config, 'ASSETS': base / 'assets', 'PF_FILE': anchor, 'STATE': root / 'state.json'}
            for key, value in replacements.items(): stack.enter_context(patch.object(engine, key, value))
            for key, value in {'RESOURCE_DIR': resources, 'PLISTS': plists, 'BACKUPS': root / 'Backups'}.items(): stack.enter_context(patch.object(installation, key, value))
            stack.enter_context(patch.object(installation.os, 'chown'))
            stack.enter_context(patch.object(installation, 'PF_CONF', pf))
            commands = []
            def run(args, *a, **kw):
                commands.append([str(v) for v in args])
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            stack.enter_context(patch.object(engine, 'run', side_effect=run))
            stack.enter_context(patch.object(engine, 'apply_kill_switch'))
            stack.enter_context(patch.object(installation, 'ensure_pf_anchor'))
            stack.enter_context(patch.object(bridge, 'start_engine'))
            stack.enter_context(patch.object(bridge, 'ready', side_effect=RuntimeError('synthetic failure') if fail else None))
            stack.enter_context(patch.object(bridge, 'client', return_value={'ok': True, 'installed': True, 'version': 9, 'helperBuild': 'development'}))
            yield root, home, commands

    def test_replacement_is_validated_and_previous_files_are_backed_up(self):
        with self.environment() as (root, home, commands):
            self.assertTrue(installation.install(501)['ok'])
            backups = list((root / 'Backups').iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'Home/preserved').read_text(), 'previous installation')
            self.assertEqual(commands[0][1:4], ['-I', '-S', '-B'])
            self.assertEqual(commands[0][-1], 'self-test')
            self.assertEqual(commands[1][1], 'check')
            self.assertEqual((home / 'Engine/configuration.json').stat().st_mode & 0o777, 0o600)
            self.assertTrue(all(command[0] == '/bin/launchctl' for command in commands[2:]))
            self.assertEqual((home / 'Python/bin/python3').read_text(), 'synthetic private interpreter')
            for label, role in (('local.fennec.engine', 'engine'), ('local.fennec.helper', 'bridge')):
                plist = plistlib.loads((root / 'LaunchDaemons' / (label + '.plist')).read_bytes())
                args = plist['ProgramArguments']
                self.assertEqual(args[:5], [str(home / 'Python/bin/python3'), '-I', '-S', '-B', str(home / 'launcher.py')])
                self.assertEqual(args[5], role)

    def test_missing_or_broken_private_runtime_never_changes_services(self):
        with self.environment() as (root, home, commands):
            (root / 'Resources/Python/bin/python3').unlink()
            with self.assertRaisesRegex(ValueError, 'private helper runtime'): installation.install(501)
            self.assertEqual(commands, [])
            self.assertTrue((home / 'preserved').exists())
        with self.environment() as (root, home, commands):
            with patch.object(engine, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(ValueError, 'runtime rejected'): installation.install(501)
            self.assertTrue((home / 'preserved').exists())
            self.assertEqual((root / 'pf.conf').read_text(), 'previous pf')
            self.assertFalse((root / 'Backups').exists())

    def test_startup_failure_restores_files_and_keeps_backup(self):
        with self.environment(fail=True) as (root, home, commands):
            with self.assertRaises(RuntimeError): installation.install(501)
            self.assertEqual((home / 'preserved').read_text(), 'previous installation')
            self.assertEqual((root / 'pf.conf').read_text(), 'previous pf')
            self.assertTrue(list((root / 'Backups').iterdir()))

    def test_unknown_saved_configuration_is_not_overwritten(self):
        with self.environment() as (root, home, commands):
            (home / 'Engine/configuration.json').unlink()
            (home / 'Engine/unknown.json').write_text('{}')
            with self.assertRaises(ValueError): installation.install(501)
            self.assertEqual(commands, [])

    def test_repair_restores_pre_transaction_configuration_before_clearing_guard(self):
        with self.environment() as (root, home, commands):
            old = config(); current = dict(old, revision=1)
            engine.atomic_json(engine.CONFIG, current)
            protection = dict(old, killSwitch=True)
            engine.atomic_json(engine.BASE / 'transaction.json', {'old': old, 'guard': protection})
            events = []
            def ready(revision):
                events.append(('ready', revision))
                self.assertTrue((engine.BASE / 'transaction.json').exists())
                self.assertTrue(engine.protected(engine.effective_guard(old)))
            with patch.object(bridge, 'ready', side_effect=ready), patch.object(bridge, 'guard', side_effect=lambda _: events.append('release')):
                self.assertTrue(installation.install(501)['ok'])
            self.assertEqual(events, [('ready', 0), 'release'])
            self.assertEqual(engine.load_configuration()['revision'], 0)
            self.assertFalse((engine.BASE / 'transaction.json').exists())
            backup = next((root / 'Backups').iterdir())
            self.assertTrue((backup / 'Home/Engine/transaction.json').exists())

    def test_failed_repair_restores_original_journal_and_stronger_guard(self):
        with self.environment(fail=True) as (root, home, commands):
            old = config(); engine.atomic_json(engine.CONFIG, dict(old, revision=1))
            engine.atomic_json(engine.BASE / 'transaction.json', {'old': old, 'guard': dict(old, killSwitch=True)})
            before = (engine.BASE / 'transaction.json').read_bytes()
            with self.assertRaises(RuntimeError): installation.install(501)
            self.assertEqual((engine.BASE / 'transaction.json').read_bytes(), before)
            self.assertEqual(engine.load_configuration()['revision'], 1)
            self.assertTrue(engine.protected(engine.apply_kill_switch.call_args[0][0]))

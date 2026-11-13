import copy
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fixtures import config
from configuration import ConfigurationError, normalize, public
from store import candidate
import bridge
import engine
import profiles
import protocol

BINARY = pathlib.Path(__file__).resolve().parents[1] / 'work/runtime-v9/sing-box'
AUTH = 'tskey-auth-synthetic-0000'


def settings(**changes):
    value = {'enabled': True, 'controlURL': 'https://control.example.invalid', 'hostname': 'laptop', 'authKey': AUTH,
             'ephemeral': False, 'acceptRoutes': False, 'route': '', 'listenPort': 41641,
             'services': [{'protocol': 'tcp', 'port': 31337, 'localPort': 31337}], 'resetLogin': False}
    value.update(changes)
    return value


def configured(base=None, **changes):
    base = normalize(base or config())
    return candidate(base, dict(action='save-tailnet', revision=base['revision'], tailnet=settings(**changes)))


def through_tailnet(value, **changes):
    route = {'id': 'private', 'title': 'Private', 'flag': '', 'enabled': True, 'nodes': [], 'exit': 'tailnet',
             'suffixes': ['srv.example.invalid'], 'overrideAddress': '100.64.0.3'}
    route.update(changes)
    return candidate(value, dict(action='save-group', revision=value['revision'], group=route))


def rules(runtime):
    return runtime['route']['rules']


class TailnetSettingsTests(unittest.TestCase):
    def test_auth_key_is_write_only_and_kept_when_omitted(self):
        value = configured()
        self.assertEqual(value['tailnet']['authKey'], AUTH)
        raw = settings(hostname='renamed'); raw.pop('authKey')
        kept = candidate(value, dict(action='save-tailnet', revision=1, tailnet=raw))
        self.assertEqual(kept['tailnet']['authKey'], AUTH)
        cleared = candidate(kept, dict(action='save-tailnet', revision=2, tailnet=settings(authKey='')))
        self.assertEqual(cleared['tailnet']['authKey'], '')
        for editing in (True, False):
            shown = public(value, editing=editing)['tailnet']
            self.assertNotIn(AUTH, json.dumps(shown))
            self.assertNotIn('stateId', shown)
        self.assertTrue(public(value, editing=True)['tailnet']['hasAuthKey'])
        self.assertEqual(set(public(value)['tailnet']), {'enabled', 'route', 'controlURL', 'hostname'})

    def test_node_identity_changes_only_with_server_or_explicit_reset(self):
        value = configured()
        state = value['tailnet']['stateId']
        self.assertRegex(state, '^[0-9a-f]{16}$')
        same = candidate(value, dict(action='save-tailnet', revision=1, tailnet=settings(hostname='other')))
        self.assertEqual(same['tailnet']['stateId'], state)
        moved = candidate(value, dict(action='save-tailnet', revision=1, tailnet=settings(controlURL='https://other.example.invalid')))
        reset = candidate(value, dict(action='save-tailnet', revision=1, tailnet=settings(resetLogin=True)))
        self.assertNotIn(state, (moved['tailnet']['stateId'], reset['tailnet']['stateId']))

    def test_requests_cannot_supply_identity_or_paths(self):
        for request in (dict(action='save-tailnet', revision=0, tailnet=settings()),
                        dict(action='set-tailnet', revision=0, enabled=True), dict(action='delete-tailnet', revision=0)):
            self.assertEqual(protocol.validate(request), request)
            with self.assertRaises(ConfigurationError): protocol.validate(dict(request, path='/tmp/input'))
        for extra in (dict(stateId='0' * 16), dict(resolved=['192.0.2.9']), dict(state_directory='/tmp')):
            with self.subTest(extra=extra), self.assertRaises(ConfigurationError):
                configured(**extra)

    def test_invalid_values_are_rejected(self):
        for change in (dict(controlURL='http://control.example.invalid'), dict(controlURL='https://user:pw@control.example.invalid'),
                       dict(controlURL='https://control.example.invalid/?x=1'), dict(hostname='Laptop'), dict(hostname='-bad'),
                       dict(authKey='with space'), dict(listenPort=80), dict(listenPort=True), dict(route='missing'),
                       dict(services=[{'protocol': 'sctp', 'port': 1, 'localPort': 1}]),
                       dict(services=[{'protocol': 'tcp', 'port': 0, 'localPort': 1}]),
                       dict(services=[{'protocol': 'tcp', 'port': 22, 'localPort': 22}] * 2), dict(enabled='yes')):
            with self.subTest(change=change), self.assertRaises(ConfigurationError):
                configured(**change)
        self.assertEqual(configured(controlURL='')['tailnet']['controlURL'], '')
        self.assertEqual(configured(controlURL='https://control.example.invalid:8443/')['tailnet']['controlURL'],
                         'https://control.example.invalid:8443')
        same_port_other_protocol = [{'protocol': 'tcp', 'port': 53, 'localPort': 53}, {'protocol': 'udp', 'port': 53, 'localPort': 53}]
        self.assertEqual(len(configured(services=same_port_other_protocol)['tailnet']['services']), 2)

    def test_routes_through_the_tailnet_need_it_and_have_no_chain(self):
        with self.assertRaises(ConfigurationError):
            through_tailnet(normalize(config()))
        value = configured()
        with self.assertRaises(ConfigurationError):
            through_tailnet(value, nodes=['sample'])
        with self.assertRaises(ConfigurationError):
            through_tailnet(value, overrideAddress='not an address')
        with self.assertRaises(ConfigurationError):
            through_tailnet(value, exit='elsewhere')
        routed = through_tailnet(value)
        group = next(g for g in routed['groups'] if g['id'] == 'private')
        self.assertEqual((group['exit'], group['overrideAddress'], group['nodes']), ('tailnet', '100.64.0.3', []))
        with self.assertRaises(ConfigurationError):
            candidate(routed, dict(action='delete-tailnet', revision=routed['revision']))
        plain = normalize(config())['groups'][0]
        self.assertNotIn('exit', plain); self.assertNotIn('overrideAddress', plain)

    def test_set_and_delete(self):
        value = configured()
        self.assertFalse(candidate(value, dict(action='set-tailnet', revision=1, enabled=False))['tailnet']['enabled'])
        self.assertNotIn('tailnet', candidate(value, dict(action='delete-tailnet', revision=1)))
        with self.assertRaises(ConfigurationError):
            candidate(normalize(config()), dict(action='set-tailnet', revision=0, enabled=True))


class TailnetRuntimeTests(unittest.TestCase):
    def runtime(self, value):
        return engine.build_runtime(value)

    def test_endpoint_dns_and_exposed_ports(self):
        value = configured(route='fallback', acceptRoutes=True, ephemeral=True)
        runtime = self.runtime(value)
        endpoint = next(e for e in runtime['endpoints'] if e['tag'] == engine.TAILNET_TAG)
        self.assertEqual(endpoint['type'], 'tailscale')
        self.assertEqual(endpoint['state_directory'], str(engine.TAILNET_DIR / value['tailnet']['stateId']))
        # launchd starts the engine in `/`, which is read-only on macOS.
        self.assertEqual(endpoint['taildrop_directory'], endpoint['state_directory'] + '/Taildrop')
        self.assertEqual((endpoint['control_url'], endpoint['hostname'], endpoint['auth_key']), ('https://control.example.invalid', 'laptop', AUTH))
        self.assertEqual((endpoint['ephemeral'], endpoint['accept_routes'], endpoint['listen_port']), (True, True, 41641))
        self.assertEqual(endpoint['detour'], 'chain_fallback_0')
        self.assertIn({'type': 'tailscale', 'tag': engine.TAILNET_DNS, 'endpoint': engine.TAILNET_TAG}, runtime['dns']['servers'])
        self.assertEqual(runtime['dns']['rules'][0], {'preferred_by': [engine.TAILNET_DNS], 'action': 'route', 'server': engine.TAILNET_DNS})
        self.assertEqual(runtime['route']['default_domain_resolver'], 'resolver')
        items = rules(runtime)
        service = items.index({'inbound': 'tailnet', 'network': 'tcp', 'port': 31337, 'action': 'route', 'outbound': 'direct',
                               'override_address': '127.0.0.1', 'override_port': 31337})
        refuse = items.index({'inbound': 'tailnet', 'action': 'reject'})
        preferred = items.index({'preferred_by': ['tailnet'], 'action': 'route', 'outbound': 'tailnet'})
        private = items.index({'ip_is_private': True, 'action': 'route', 'outbound': 'direct'})
        self.assertLess(service, refuse); self.assertLess(refuse, preferred); self.assertLess(preferred, private)
        # A private resolver behind the tailnet (corporate DNS named in /etc/resolver) is
        # reached as is; every other DNS query is still answered by the engine.
        passthrough = items.index({'protocol': 'dns', 'ip_is_private': True, 'preferred_by': ['tailnet'],
                                   'action': 'route', 'outbound': 'tailnet'})
        hijack = items.index({'protocol': 'dns', 'action': 'hijack-dns'})
        self.assertEqual(items[0], {'action': 'sniff'})
        self.assertLess(passthrough, hijack)

    def test_empty_optional_fields_use_sing_box_defaults(self):
        endpoint = self.runtime(configured(controlURL='', hostname='', authKey=''))['endpoints'][0]
        for key in ('control_url', 'hostname', 'auth_key', 'detour'):
            self.assertNotIn(key, endpoint)

    def test_route_through_the_tailnet_overrides_the_destination(self):
        runtime = self.runtime(through_tailnet(configured()))
        self.assertIn({'domain_suffix': ['srv.example.invalid'], 'action': 'route', 'outbound': 'tailnet',
                       'override_address': '100.64.0.3'}, rules(runtime))
        self.assertNotEqual(runtime['dns']['servers'][0].get('detour'), 'tailnet')

    def test_disabled_tailnet_leaves_its_routes_off(self):
        value = through_tailnet(configured())
        off = candidate(value, dict(action='set-tailnet', revision=value['revision'], enabled=False))
        runtime = self.runtime(off)
        self.assertNotIn('endpoints', runtime)
        self.assertIn({'domain_suffix': ['srv.example.invalid'], 'action': 'route', 'outbound': 'direct'}, rules(runtime))
        protected = through_tailnet(off, killSwitch=True)
        self.assertIn({'domain_suffix': ['srv.example.invalid'], 'action': 'reject', 'method': 'default'}, rules(self.runtime(protected)))

    def test_runtime_without_tailnet_is_unchanged(self):
        runtime = self.runtime(normalize(config()))
        self.assertNotIn('endpoints', runtime)
        self.assertNotIn('default_domain_resolver', runtime['route'])
        self.assertFalse(any('preferred_by' in rule for rule in rules(runtime) + runtime['dns']['rules']))

    def test_idle_tailnet_runs_without_tun(self):
        value = configured()
        idle = candidate(value, dict(action='set-bypass', revision=value['revision'], enabled=True))
        runtime = self.runtime(idle)
        self.assertEqual([i['type'] for i in runtime['inbounds']], ['mixed'])
        self.assertEqual(runtime['endpoints'][0]['tag'], 'tailnet')

    def test_pf_protection_opens_only_the_tailnet_port_and_direct_control_server(self):
        value = configured()
        value['tailnet']['resolved'] = ['198.51.100.7']
        value['fallback']['killSwitch'] = True
        lines = engine.build_pf_rules(value, 'utun9').splitlines()
        self.assertIn('pass out quick proto udp from any port 41641 keep state', lines)
        self.assertIn('pass out quick inet proto tcp to 198.51.100.7 port 443 keep state', lines)
        chained = configured(route='fallback'); chained['tailnet']['resolved'] = ['198.51.100.7']; chained['fallback']['killSwitch'] = True
        self.assertFalse(any('198.51.100.7' in line for line in engine.build_pf_rules(chained, 'utun9').splitlines()))
        self.assertEqual(engine.build_pf_rules(normalize(config()), 'utun9'), '')

    def test_control_host_is_resolved_only_for_a_direct_connection(self):
        value = configured()
        with patch.object(engine.socket, 'getaddrinfo', return_value=[(None, None, None, None, ('198.51.100.7', 443))]):
            engine.resolve_nodes(value)
        self.assertEqual(value['tailnet']['resolved'], ['198.51.100.7'])
        chained = configured(route='fallback')
        with patch.object(engine.socket, 'getaddrinfo', side_effect=AssertionError('must not resolve')):
            engine.resolve_nodes(chained)
        failed = configured()
        with patch.object(engine.socket, 'getaddrinfo', side_effect=OSError):
            engine.resolve_nodes(failed)
        self.assertEqual(failed['tailnet']['resolved'], [])


class EngineEnvironmentTests(unittest.TestCase):
    def test_engine_runs_without_log_upload_from_its_own_directory(self):
        import inspect
        self.assertEqual(engine.ENGINE_ENVIRONMENT['TS_NO_LOGS_NO_SUPPORT'], 'true')
        source = inspect.getsource(engine.serve)
        self.assertIn('env=ENGINE_ENVIRONMENT', source)
        self.assertIn('cwd=str(BASE)', source)


class TailnetStateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        base = pathlib.Path(self.folder.name)
        for name, path in (('BASE', base), ('TAILNET_DIR', base / 'tailnet')):
            patcher = patch.object(engine, name, path); patcher.start(); self.addCleanup(patcher.stop)

    def test_registration_reads_only_the_profile_marker(self):
        value = configured()
        self.assertFalse(engine.tailnet_registered(value))
        folder = engine.TAILNET_DIR / value['tailnet']['stateId']; folder.mkdir(parents=True)
        (folder / 'tailscaled.state').write_text(json.dumps({'_current-profile': 'YWJj', 'profile-abc': 'c2VjcmV0'}))
        self.assertTrue(engine.tailnet_registered(value))
        (folder / 'tailscaled.state').write_text('not json')
        self.assertFalse(engine.tailnet_registered(value))

    def test_prune_keeps_the_current_identity_and_waits_for_transactions(self):
        value = configured()
        keep = engine.TAILNET_DIR / value['tailnet']['stateId']; old = engine.TAILNET_DIR / ('a' * 16); other = engine.TAILNET_DIR / 'notes'
        for path in (keep, old, other): path.mkdir(parents=True)
        (engine.BASE / 'transaction.json').write_text('{}')
        engine.prune_tailnet(value)
        self.assertTrue(old.exists())
        (engine.BASE / 'transaction.json').unlink()
        engine.prune_tailnet(value)
        self.assertEqual((keep.exists(), old.exists(), other.exists()), (True, False, True))
        engine.prune_tailnet(normalize(config()))
        self.assertFalse(keep.exists())

    def test_status_states(self):
        value = configured()
        self.assertEqual(bridge.tailnet_state(normalize(config()), {}), 'off')
        self.assertEqual(bridge.tailnet_state(value, {'state': 'error'}), 'stopped')
        self.assertEqual(bridge.tailnet_state(value, {'state': 'on'}), 'waiting')
        without_key = candidate(value, dict(action='save-tailnet', revision=1, tailnet=settings(authKey='')))
        self.assertEqual(bridge.tailnet_state(without_key, {'state': 'on'}), 'login')
        with patch.dict(bridge.tailnet_probe, revision=without_key['revision'], connected=True):
            self.assertEqual(bridge.tailnet_state(without_key, {'state': 'on'}), 'connected')


class TailnetProfileTests(unittest.TestCase):
    def test_profile_omits_the_key_and_identity(self):
        value = configured(); value['tailnet']['resolved'] = ['198.51.100.7']
        with tempfile.TemporaryDirectory() as folder:
            archive = profiles.pack(value, folder)
        imported, _, _ = profiles.unpack(archive, 5)
        self.assertEqual(imported['tailnet']['authKey'], '')
        self.assertEqual(imported['tailnet']['resolved'], [])
        self.assertNotEqual(imported['tailnet']['stateId'], value['tailnet']['stateId'])
        self.assertEqual(imported['tailnet']['services'], value['tailnet']['services'])
        import base64, io, zipfile
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(archive))) as zipped:
            text = zipped.read('configuration.json').decode()
        for secret in (AUTH, value['tailnet']['stateId'], '198.51.100.7'):
            self.assertNotIn(secret, text)
        self.assertTrue(profiles.summary(imported, {}, 'x')['tailnet'])


@unittest.skipUnless(BINARY.exists(), 'Runs after the pinned runtime download')
class PinnedTailnetTests(unittest.TestCase):
    def test_pinned_sing_box_accepts_the_runtime(self):
        value = through_tailnet(configured(route='fallback'))
        with tempfile.TemporaryDirectory() as folder:
            for runtime in (engine.build_runtime(value), engine.build_runtime(candidate(value, dict(action='set-bypass', revision=value['revision'], enabled=True)))):
                path = pathlib.Path(folder) / 'runtime.json'
                path.write_text(json.dumps(runtime))
                result = subprocess.run([str(BINARY), 'check', '-c', str(path)], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

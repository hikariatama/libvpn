import copy
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from fixtures import config, node
from configuration import ConfigurationError, normalize, public
from store import candidate
import bridge
import engine
import profiles
import protocol

KEY = 'A' * 43 + '='
OTHER = 'B' * 43 + '='


def remote(**changes):
    value = {'enabled': True, 'address': '203.0.113.5', 'port': 51820, 'serverPublicKey': KEY,
             'tunnelAddress': '10.77.0.2', 'peerAddress': '10.77.0.1', 'localPort': 3773, 'route': 'fallback', 'rotateKeys': False}
    value.update(changes)
    return value


def configured(**changes):
    return candidate(normalize(config()), dict(action='save-remote-access', revision=0, remote=remote(**changes)), lambda: (OTHER, KEY))


class RemoteAccessTests(unittest.TestCase):
    def test_keys_are_generated_once_and_kept_until_rotation(self):
        value = configured()
        self.assertEqual((value['remoteAccess']['privateKey'], value['remoteAccess']['publicKey']), (OTHER, KEY))
        kept = candidate(value, dict(action='save-remote-access', revision=1, remote=remote(localPort=4000)), lambda: self.fail('Keys must be kept'))
        self.assertEqual(kept['remoteAccess']['privateKey'], OTHER)
        rotated = candidate(kept, dict(action='save-remote-access', revision=2, remote=remote(rotateKeys=True)), lambda: (KEY, OTHER))
        self.assertEqual(rotated['remoteAccess']['publicKey'], OTHER)

    def test_private_key_never_leaves_the_helper(self):
        value = configured()
        for editing in (True, False):
            self.assertNotIn(OTHER, str(public(value, editing=editing)['remoteAccess'].get('privateKey', '')))
            self.assertNotIn('privateKey', public(value, editing=editing)['remoteAccess'])
        self.assertEqual(set(public(value)['remoteAccess']), {'enabled', 'route', 'localPort'})
        self.assertEqual(public(value, editing=True)['remoteAccess']['publicKey'], KEY)

    def test_requests_are_closed_and_cannot_supply_keys_or_paths(self):
        for request in (dict(action='save-remote-access', revision=0, remote=remote()),
                        dict(action='set-remote-access', revision=0, enabled=True), dict(action='delete-remote-access', revision=0)):
            self.assertEqual(protocol.validate(request), request)
            with self.assertRaises(ConfigurationError): protocol.validate(dict(request, path='/tmp/input'))
        with self.assertRaises(ConfigurationError):
            candidate(normalize(config()), dict(action='save-remote-access', revision=0, remote=remote(privateKey=KEY)), lambda: (OTHER, KEY))

    def test_invalid_values_are_rejected(self):
        for change in (dict(localPort=0), dict(localPort=True), dict(port=70000), dict(serverPublicKey='wrong'), dict(tunnelAddress='8.8.8.8'),
                       dict(peerAddress='10.77.0.2'), dict(peerAddress='fd00::1'), dict(tunnelAddress='127.0.0.2'), dict(route='missing'), dict(address='bad host')):
            with self.subTest(change=change), self.assertRaises(ConfigurationError):
                configured(**change)

    def test_routes_used_by_remote_access_cannot_lose_their_chain(self):
        value = configured(route='custom')
        with self.assertRaises(ConfigurationError): candidate(value, dict(action='delete-group', revision=1, id='custom'))
        with self.assertRaises(ConfigurationError): candidate(value, dict(action='set-chain', revision=1, id='custom', nodes=[]))
        disabled = candidate(value, dict(action='set-enabled', revision=1, id='custom', enabled=False))
        self.assertFalse(disabled['groups'][0]['enabled'])

    def test_set_and_delete(self):
        value = configured()
        self.assertFalse(candidate(value, dict(action='set-remote-access', revision=1, enabled=False))['remoteAccess']['enabled'])
        self.assertNotIn('remoteAccess', candidate(value, dict(action='delete-remote-access', revision=1)))
        with self.assertRaises(ConfigurationError): candidate(normalize(config()), dict(action='delete-remote-access', revision=0))

    def test_runtime_exposes_only_the_selected_loopback_port_through_the_chain(self):
        runtime = engine.build_runtime(configured())
        endpoint = runtime['endpoints'][0]
        self.assertEqual(endpoint['detour'], 'chain_fallback_0')
        self.assertEqual(endpoint['peers'][0]['allowed_ips'], ['10.77.0.1/32'])
        self.assertEqual(endpoint['address'], ['10.77.0.2/32'])
        self.assertFalse(endpoint['system'])
        rules = runtime['route']['rules']
        allow = next(i for i, r in enumerate(rules) if r.get('inbound') == 'remote-access' and r['action'] == 'route')
        self.assertEqual(rules[allow]['override_address'], '127.0.0.1')
        self.assertEqual((rules[allow]['port'], rules[allow]['override_port']), (3773, 3773))
        self.assertEqual(rules[allow + 1], {'inbound': 'remote-access', 'action': 'reject'})
        private = next(i for i, r in enumerate(rules) if r.get('ip_is_private'))
        self.assertLess(allow + 1, private)
        probe = next(r for r in rules if r.get('outbound') == 'remote-access')
        self.assertEqual((probe['inbound'], probe['ip_cidr']), ('endpoint-dns', ['10.77.0.1/32']))

    def test_runtime_without_remote_access_is_unchanged(self):
        runtime = engine.build_runtime(normalize(config()))
        self.assertNotIn('endpoints', runtime)
        self.assertEqual(runtime['inbounds'][0]['type'], 'tun')
        disabled = candidate(configured(), dict(action='set-remote-access', revision=1, enabled=False))
        self.assertNotIn('endpoints', engine.build_runtime(disabled))
        self.assertFalse(any(r.get('inbound') == 'remote-access' for r in engine.build_runtime(disabled)['route']['rules']))

    def test_direct_route_has_no_detour(self):
        self.assertNotIn('detour', engine.build_runtime(configured(route=''))['endpoints'][0])

    def test_bypass_and_idle_keep_remote_access_without_tun(self):
        value = configured()
        bypass = candidate(value, dict(action='set-bypass', revision=1, enabled=True))
        for state in (bypass, self.idle(value)):
            self.assertTrue(engine.idle(state))
            runtime = engine.build_runtime(state)
            self.assertEqual([i['type'] for i in runtime['inbounds']], ['mixed'])
            self.assertEqual(runtime['endpoints'][0]['tag'], 'remote-access')
        self.assertEqual(runtime['inbounds'][0]['tag'], 'endpoint-dns')

    def test_tun_terminates_tcp_inside_sing_box(self):
        # The system stack lost every TCP connection when another VPN's utun was up at start.
        runtime = engine.build_runtime(configured())
        tun = next(i for i in runtime['inbounds'] if i['type'] == 'tun')
        self.assertEqual(tun['stack'], 'gvisor')

    def idle(self, value):
        result = copy.deepcopy(value)
        for group in result['groups'] + [result['fallback']]: group['enabled'] = False
        return normalize(result)

    def test_hostname_is_resolved_like_servers(self):
        value = configured(address='vps.example.invalid')
        with self.assertRaises(ConfigurationError): engine.build_runtime(value)
        with patch.object(engine.socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('203.0.113.9', 0))]):
            engine.resolve_nodes(value)
        self.assertEqual(engine.build_runtime(value)['endpoints'][0]['peers'][0]['address'], '203.0.113.9')

    def test_profile_keeps_credentials_but_not_cached_addresses(self):
        value = configured(address='vps.example.invalid'); value['remoteAccess']['resolved'] = ['203.0.113.9']
        with tempfile.TemporaryDirectory() as folder:
            archive = profiles.pack(value, folder)
        restored, blobs, digest = profiles.unpack(archive, 4)
        self.assertEqual(restored['remoteAccess']['privateKey'], OTHER)
        self.assertEqual(restored['remoteAccess']['resolved'], [])
        self.assertTrue(profiles.summary(restored, blobs, digest)['remoteAccess'])

    def test_status_states(self):
        value = configured()
        with patch.dict(bridge.remote_probe, revision=None, connected=False):
            self.assertEqual(bridge.remote_state(normalize(config()), {'state': 'on'}), 'off')
            self.assertEqual(bridge.remote_state(value, {'state': 'on'}), 'waiting')
            self.assertEqual(bridge.remote_state(value, {'state': 'error'}), 'stopped')
            bridge.remote_probe.update(revision=value['revision'], connected=True)
            self.assertEqual(bridge.remote_state(value, {'state': 'bypass'}), 'connected')
            bridge.remote_probe.update(revision=value['revision'] - 1)
            self.assertEqual(bridge.remote_state(value, {'state': 'on'}), 'waiting')

    def probe(self, recv):
        token = tempfile.NamedTemporaryFile('w', suffix='.token'); token.write('secret'); token.flush()
        self.addCleanup(token.close)
        proxy = MagicMock(); proxy.__enter__.return_value = proxy
        proxy.recv.side_effect = recv
        with patch.object(engine, 'TOKEN', pathlib.Path(token.name)), patch.object(engine.socket, 'create_connection', return_value=proxy):
            return engine.remote_reachable(configured()), proxy

    def test_probe_counts_a_prompt_close_as_a_working_tunnel(self):
        result, proxy = self.probe([b'\x05\x02', b'\x01\x00', b'\x05\x00\x00\x01', b'\0\0\0\0', b'\0\0', b''])
        self.assertTrue(result)
        sent = b''.join(c.args[0] for c in proxy.sendall.call_args_list)
        self.assertIn(bytes([10, 77, 0, 1]), sent)
        self.assertIn(b'secret', sent)

    def test_probe_treats_silence_after_the_optimistic_reply_as_no_path(self):
        result, _ = self.probe([b'\x05\x02', b'\x01\x00', b'\x05\x00\x00\x01', b'\0\0\0\0', b'\0\0', engine.socket.timeout()])
        self.assertFalse(result)

    def test_probe_failure_is_not_connected(self):
        with patch.object(engine, 'TOKEN', pathlib.Path('/nonexistent')):
            with self.assertRaises(OSError): engine.remote_reachable(configured())
        value = configured()
        with patch.object(engine, 'remote_reachable', side_effect=OSError), patch.object(engine, 'load_configuration', return_value=value), patch.object(engine, 'read_state', return_value={'state': 'on'}):
            with self.assertRaises(OSError): bridge.probe_remote_once()

    def test_pf_guard_does_not_open_the_remote_server(self):
        value = configured()
        self.assertNotIn('203.0.113.5', engine.build_pf_rules(dict(value, killSwitch=True)))

    @unittest.skipUnless(pathlib.Path('work/runtime-v9/sing-box').exists(), 'Runs after the pinned runtime download')
    def test_pinned_sing_box_accepts_runtime_and_generates_keys(self):
        binary = pathlib.Path('work/runtime-v9/sing-box').resolve()
        with patch.object(engine, 'BIN', binary):
            private, public_key = engine.wireguard_keys()
        value = candidate(normalize(config()), dict(action='save-remote-access', revision=0, remote=remote(serverPublicKey=public_key)), lambda: (private, public_key))
        for state in (value, candidate(value, dict(action='set-bypass', revision=1, enabled=True))):
            with tempfile.TemporaryDirectory() as folder:
                path = pathlib.Path(folder) / 'runtime.json'
                engine.atomic_json(path, engine.build_runtime(state, assets=folder))
                result = subprocess.run([binary, 'check', '-c', path], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

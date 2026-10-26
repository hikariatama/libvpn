import copy
import json
import pathlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fixtures import config, node
from configuration import empty_configuration, ConfigurationError
import engine


class EngineTests(unittest.TestCase):
    def test_chains_use_ordered_detours(self):
        value = config(); value['nodes'].append(node('next', '198.51.100.2'))
        value['groups'][0]['nodes'].append('next')
        runtime = engine.build_runtime(value)
        outbounds = {v['tag']: v for v in runtime['outbounds']}
        self.assertEqual(outbounds['chain_custom_1']['detour'], 'chain_custom_0')
        self.assertNotIn('detour', outbounds['chain_custom_0'])

    def test_rule_precedence_and_or_semantics(self):
        value = config(); value['groups'][0]['suffixes'] = ['example.org'];value['groups'][0]['cidrs'] = ['198.51.100.0/24']
        runtime = engine.build_runtime(value)
        matches = [r for r in runtime['route']['rules'] if r.get('outbound') == 'chain_custom_0']
        self.assertEqual(len(matches), 3)
        self.assertTrue(all(sum(k in r for k in ('domain', 'domain_suffix', 'ip_cidr')) == 1 for r in matches))
        self.assertEqual(runtime['route']['rules'][-1]['outbound'], 'chain_fallback_0')

    def test_disabled_group_rejects_when_guarded_otherwise_direct(self):
        value = config();value['groups'][0]['enabled'] = False
        for protected, expected in ((False, 'route'), (True, 'reject')):
            value['killSwitch'] = protected
            rule = next(r for r in engine.build_runtime(value)['route']['rules'] if 'domain' in r)
            self.assertEqual(rule['action'], expected)

    def test_ipv4_ipv6_guard_is_independent_of_physical_interface(self):
        value = config();value['killSwitch'] = True;value['nodes'].append(node('v6', '2001:db8::1'))
        rules = engine.build_pf_rules(value, 'utun42')
        self.assertIn('inet6 proto tcp to 2001:db8::1 port 443', rules)
        self.assertIn('pass out quick on utun42 all', rules)
        self.assertTrue(rules.endswith('block drop out quick all\n'))
        self.assertNotIn('utun', engine.build_pf_rules(value))
        if sys.platform == 'darwin':
            with tempfile.TemporaryDirectory() as folder:
                path = pathlib.Path(folder) / 'guard.pf'
                for tunnel in (None, 'utun42'):
                    path.write_text(engine.build_pf_rules(value, tunnel))
                    result = subprocess.run(['/sbin/pfctl', '-n', '-a', engine.PF_ANCHOR, '-f', str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(rules.index('port { 53, 853 }'), rules.index('10.0.0.0/8'))
        value['killSwitch'] = False;self.assertEqual(engine.build_pf_rules(value), '')

    def test_protected_hostname_lookup_never_falls_back_to_system_dns(self):
        value = config();value['nodes'][0]['address'] = 'example.invalid'
        with patch.object(engine, 'tunneled_lookup', side_effect=OSError), patch.object(engine.socket, 'getaddrinfo') as system:
            with self.assertRaises(ConfigurationError): engine.resolve_nodes(value, True)
            system.assert_not_called()

    def test_hostname_cache_and_tunnel_lookup(self):
        value = config();value['nodes'][0]['address'] = 'example.invalid'
        with patch.object(engine, 'tunneled_lookup', return_value=['192.0.2.1']) as lookup:
            engine.resolve_nodes(value, True)
            engine.resolve_nodes(value, True)
            self.assertEqual(lookup.call_count, 1)
            engine.resolve_nodes(value, True, refresh=True)
            self.assertEqual(lookup.call_count, 2)

    def test_runtime_has_no_diagnostic_endpoints_or_credentials_in_logs(self):
        runtime = engine.build_runtime(config())
        self.assertEqual(runtime['log'], {'disabled': True})
        self.assertTrue(any(':' in address for address in runtime['inbounds'][0]['address']))
        proxy = runtime['inbounds'][1]
        self.assertEqual(proxy['listen'], '127.0.0.1')
        self.assertTrue(proxy['users'])

    def test_real_runtime_check_uses_only_build_artifact(self):
        binary = pathlib.Path(__file__).resolve().parents[1] / 'work/runtime-v9/sing-box'
        if not binary.is_file(): self.skipTest('Run ./build.sh to prepare the pinned test runtime')
        for value in (config(), empty_configuration()):
            for protected in (False, True):
                value['killSwitch'] = protected
                with tempfile.TemporaryDirectory() as folder:
                    path = pathlib.Path(folder) / 'runtime.json';path.write_text(json.dumps(engine.build_runtime(value)))
                    result = subprocess.run([str(binary), 'check', '-c', str(path)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_compiled_snapshots_are_valid_in_route_and_dns_rules(self):
        import hashlib
        binary = pathlib.Path(__file__).resolve().parents[1] / 'work/runtime-v9/sing-box'
        if not binary.is_file(): self.skipTest('Build runtime is not prepared')
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            source = folder / 'rules.json'
            source.write_text(json.dumps({'version': 3, 'rules': [{'domain_suffix': ['example.invalid']}, {'ip_cidr': ['198.51.100.0/24', '2001:db8::/32']}]}))
            compiled = folder / 'rules.srs'
            result = subprocess.run([str(binary), 'rule-set', 'compile', '-o', str(compiled), str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            digest = hashlib.sha256(compiled.read_bytes()).hexdigest(); compiled.rename(folder / (digest + '.srs'))
            value = config(); value['groups'][0].update(snapshots=[digest], enabled=False, killSwitch=True)
            path = folder / 'runtime.json'; path.write_text(json.dumps(engine.build_runtime(value, folder)))
            result = subprocess.run([str(binary), 'check', '-c', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_inter_node_transport_survives_without_becoming_a_direct_exception(self):
        value = config();value['nodes'].append(node('next', '198.51.100.2'))
        value['nodes'][0].update(private_address='10.1.0.1', private_network='fabric')
        value['nodes'][1].update(private_address='10.1.0.2', private_network='fabric')
        value['groups'][0]['nodes'].append('next')
        outbound = next(v for v in engine.build_runtime(value)['outbounds'] if v['tag'] == 'chain_custom_1')
        self.assertEqual(outbound['server'], '10.1.0.2')
        self.assertEqual(outbound['detour'], 'chain_custom_0')

    def test_pf_enable_reference_is_idempotent_per_boot(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, 'PF_REFERENCE', pathlib.Path(folder) / 'pf-reference.json'):
            calls = []; enabled = [False]
            def run(args, **kw):
                calls.append(args)
                if args == ['/sbin/pfctl', '-E']: enabled[0] = True
                return SimpleNamespace(returncode=0, stdout='synthetic-boot' if args[0] == '/usr/sbin/sysctl' else 'Status: Enabled' if enabled[0] else 'Status: Disabled')
            with patch.object(engine, 'run', side_effect=run):
                engine.ensure_pf_enabled();engine.ensure_pf_enabled()
                self.assertEqual(calls.count(['/sbin/pfctl', '-E']), 1)
                enabled[0] = False
                engine.ensure_pf_enabled()
            self.assertEqual(calls.count(['/sbin/pfctl', '-E']), 2)

    def test_concurrent_unrelated_tunnel_is_never_whitelisted(self):
        with patch.object(engine, 'interfaces', return_value={'en0', 'utun1', 'utun2', 'utun3'}), \
             patch.object(engine, 'run', side_effect=lambda args, **kw: SimpleNamespace(returncode=0, stdout='inet 172.19.0.1 --> 172.19.0.1 netmask 0xfffffffc' if args[-1] == 'utun3' else 'inet 10.0.0.1')):
            self.assertEqual(engine.own_tunnel({'en0', 'utun1'}), 'utun3')
        with patch.object(engine, 'interfaces', return_value={'en0', 'utun2'}), \
             patch.object(engine, 'run', return_value=SimpleNamespace(returncode=0, stdout='inet 10.0.0.1')):
            self.assertIsNone(engine.own_tunnel({'en0'}))

import copy
import pathlib
import unittest
from unittest.mock import patch
from fixtures import config, group, node
from configuration import ConfigurationError, empty_configuration, normalize
from store import candidate
import engine
import protocol


class RouteControlTests(unittest.TestCase):
    def test_route_protection_is_independent_and_keeps_first_match_order(self):
        value = normalize(config())
        value['groups'].append(dict(group('second'), domains=['second.invalid'], enabled=False))
        value['groups'][0].update(enabled=False, killSwitch=True)
        rules = engine.build_runtime(value)['route']['rules']
        routes = [r for r in rules if 'domain' in r]
        self.assertEqual([r['domain'] for r in routes], [['example.invalid'], ['second.invalid']])
        self.assertEqual(routes[0]['action'], 'reject')
        self.assertEqual(routes[1]['outbound'], 'direct')
        self.assertEqual(rules[-1]['outbound'], 'chain_fallback_0')

    def test_switch_and_chain_changes_touch_only_selected_route(self):
        original = normalize(config())
        original['nodes'].append(node('second', '198.51.100.2'))
        value = candidate(original, dict(action='set-kill-switch', revision=0, id='custom', enabled=True))
        self.assertTrue(value['groups'][0]['killSwitch'])
        self.assertEqual(value['fallback'], original['fallback'])
        changed = candidate(value, dict(action='set-chain', revision=1, id='custom', nodes=['second', 'sample']))
        self.assertEqual(changed['groups'][0]['nodes'], ['second', 'sample'])
        self.assertTrue(changed['groups'][0]['killSwitch'])
        self.assertEqual(original['groups'][0]['nodes'], ['sample'])
        with self.assertRaises(ConfigurationError):
            candidate(value, dict(action='set-chain', revision=1, id='custom', nodes=['sample', 'sample']))

    def test_bypass_preserves_routes_and_restores_protection(self):
        value = normalize(config()); value['groups'][0]['killSwitch'] = True
        bypass = candidate(value, dict(action='set-bypass', revision=0, enabled=True))
        self.assertEqual(bypass['groups'], value['groups'])
        self.assertEqual(bypass['fallback'], value['fallback'])
        self.assertEqual(engine.build_pf_rules(bypass), '')
        runtime = engine.build_runtime(bypass)
        self.assertTrue(all(r.get('outbound', 'direct') == 'direct' for r in runtime['route']['rules']))
        restored = candidate(bypass, dict(action='set-bypass', revision=1, enabled=False))
        self.assertTrue(engine.protected(restored))
        self.assertEqual(restored['groups'], value['groups'])

    def test_guard_does_not_block_unprotected_routes_when_tun_is_ready(self):
        value = normalize(config()); value['groups'][0]['killSwitch'] = True
        live = engine.build_pf_rules(value, 'utun42')
        self.assertTrue(live.endswith('pass out all\n'))
        self.assertIn('port { 53, 853 }', live)
        self.assertTrue(engine.build_pf_rules(value).endswith('block drop out quick all\n'))

    def test_existing_global_protection_expands_without_disabling_any_route(self):
        value = config(); value['killSwitch'] = True
        value['groups'][0]['killSwitch'] = False
        normalized = normalize(value)
        self.assertTrue(normalized['groups'][0]['killSwitch'])
        self.assertTrue(normalized['fallback']['killSwitch'])
        self.assertFalse(normalized['killSwitch'])
        self.assertEqual(normalize(normalized), normalized)

    def test_controls_use_closed_revision_checked_requests(self):
        for action in ('set-chain', 'set-kill-switch', 'set-bypass'):
            request = dict(action=action, revision=0)
            if action != 'set-bypass': request['id'] = 'custom'
            request['nodes' if action == 'set-chain' else 'enabled'] = ['sample'] if action == 'set-chain' else True
            self.assertEqual(protocol.validate(request), request)
            with self.assertRaises(ConfigurationError): protocol.validate(dict(request, path='/tmp/input'))
        with self.assertRaises(ConfigurationError):
            protocol.validate(dict(action='set-kill-switch', revision=0, enabled=True))
        value = normalize(config())
        with self.assertRaises(ConfigurationError): candidate(value, dict(action='set-chain', revision=1, id='custom', nodes=['sample']))
        self.assertEqual(value['revision'], 0)

    def test_disabled_domain_dns_is_blocked_without_blocking_other_names(self):
        value = normalize(config())
        value['groups'][0].update(enabled=False, killSwitch=True)
        value['fallback']['enabled'] = False
        dns = engine.build_runtime(value)['dns']
        self.assertEqual(dns['rules'][0], {'domain': ['example.invalid'], 'action': 'reject'})
        self.assertEqual(dns['servers'][0]['type'], 'https')
        self.assertNotIn('detour', dns['servers'][0])
        self.assertEqual(dns['final'], 'resolver')

    def test_new_install_has_no_protection_bypass_or_custom_defaults(self):
        value = normalize(empty_configuration())
        self.assertFalse(engine.protected(value)); self.assertFalse(value['bypass'])
        self.assertEqual(value['nodes'], []); self.assertEqual(value['groups'], [])

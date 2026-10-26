import copy
import json
import unittest
from fixtures import config, node
import configuration as c
from store import candidate


class ConfigurationTests(unittest.TestCase):
    def test_empty_installation_is_direct_and_unprotected(self):
        value = c.normalize(c.empty_configuration())
        self.assertEqual(value['nodes'], []); self.assertEqual(value['groups'], [])
        self.assertFalse(value['killSwitch']); self.assertFalse(value['fallback']['enabled'])

    def test_import_and_reject_incompatible_link_parameters(self):
        prefix = 'vless://00000000-0000-4000-8000-000000000001@example.invalid:8443?security=tls&type=tcp'
        value = c.import_vless(prefix + '&sni=tls.example.invalid#Example')
        self.assertEqual(value['port'], 8443)
        self.assertEqual(value['tls']['server_name'], 'tls.example.invalid')
        self.assertNotIn('certificate_public_key_sha256', value['tls'])
        for ending in ('&type=ws', '&allowInsecure=1', '&security=reality', '&flow=xtls-rprx-vision', '&fp=chrome', '&path=/ws'):
            with self.subTest(ending=ending), self.assertRaises(c.ConfigurationError):
                c.import_vless(prefix + ending)
        for link in (prefix.replace(':8443', ':0'), prefix.replace('security=tls', 'security=none'), 'not-a-link'):
            with self.assertRaises(c.ConfigurationError): c.import_vless(link)

    def test_ipv6_and_idna_endpoints(self):
        prefix = 'vless://00000000-0000-4000-8000-000000000001@'
        self.assertEqual(c.import_vless(prefix + '[2001:db8::1]:443?security=tls')['address'], '2001:db8::1')
        self.assertTrue(c.hostname('пример.example').startswith('xn--'))

    def test_pin_uuid_and_boolean_validation(self):
        value = node(); value['tls']['certificate_public_key_sha256'] = ['A' * 43 + '=']
        self.assertEqual(len(c.node(value)['tls']['certificate_public_key_sha256']), 1)
        for mutate in (lambda v: v.update(uuid='wrong'), lambda v: v.update(port=True),
                       lambda v: v['tls'].update(certificate_public_key_sha256=['wrong']),
                       lambda v: v['tls'].update(insecure=True)):
            value = node(); mutate(value)
            with self.assertRaises(c.ConfigurationError): c.node(value)

    def test_tls_version_constraints_are_preserved_and_validated(self):
        value = node();value['tls'].update(min_version='1.3', max_version='1.3')
        self.assertEqual(c.node(value)['tls']['min_version'], '1.3')
        changed = candidate(config(), {'action': 'save-node', 'revision': 0, 'node': value})
        self.assertEqual(changed['nodes'][0]['tls']['max_version'], '1.3')
        for low, high in (('1.3', '1.2'), ('1.0', '1.3')):
            value['tls'].update(min_version=low, max_version=high)
            with self.assertRaises(c.ConfigurationError): c.node(value)

    def test_groups_normalize_networks_and_reject_cycles(self):
        value = config(); value['groups'][0]['cidrs'] = ['2001:db8::1/64', '192.0.2.4']
        normalized = c.normalize(value)
        self.assertEqual(normalized['groups'][0]['cidrs'], ['2001:db8::/64', '192.0.2.4/32'])
        value['groups'][0]['nodes'] *= 2
        with self.assertRaises(c.ConfigurationError): c.normalize(value)

    def test_used_node_cannot_be_removed(self):
        value = config()
        with self.assertRaisesRegex(c.ConfigurationError, 'Custom'):
            candidate(value, {'action': 'delete-node', 'revision': 0, 'id': 'sample'})
        self.assertEqual(len(value['nodes']), 1)

    def test_secret_fields_are_write_only_and_can_be_cleared_explicitly(self):
        value = config(); value['nodes'][0]['tls']['certificate_public_key_sha256'] = ['A' * 43 + '=']
        raw = node(); raw['uuid'] = ''; raw['clearPin'] = False
        new = candidate(value, {'action': 'save-node', 'revision': 0, 'node': raw})
        self.assertEqual(new['nodes'][0]['uuid'], value['nodes'][0]['uuid'])
        self.assertIn('certificate_public_key_sha256', new['nodes'][0]['tls'])
        raw['clearPin'] = True
        new = candidate(value, {'action': 'save-node', 'revision': 0, 'node': raw})
        self.assertNotIn('certificate_public_key_sha256', new['nodes'][0]['tls'])

    def test_public_status_and_edit_projection_do_not_expose_credentials(self):
        value = c.normalize(config())
        value['groups'][0]['cidrs'] = ['198.51.100.0/24']
        self.assertNotIn('198.51.100.0/24', json.dumps(c.public(value)))
        for editing in (False, True):
            result = c.public(value, editing)
            self.assertNotIn(value['nodes'][0]['uuid'], json.dumps(result))
            self.assertNotIn('certificate_public_key_sha256', json.dumps(result))
        self.assertNotIn('address', c.public(value)['nodes'][0])
        self.assertEqual(c.public(value, True)['nodes'][0]['address'], '192.0.2.1')

    def test_revisions_order_and_snapshot_ownership(self):
        value = config(); second = copy.deepcopy(value['groups'][0]); second['id'] = 'second'; value['groups'].append(second)
        result = candidate(value, {'action': 'reorder-groups', 'revision': 0, 'ids': ['second', 'custom']})
        self.assertEqual([g['id'] for g in result['groups']], ['second', 'custom'])
        with self.assertRaises(c.ConfigurationError): candidate(result, {'action': 'delete-group', 'revision': 0, 'id': 'custom'})
        second = copy.deepcopy(second); second['snapshots'] = ['a' * 64]
        with self.assertRaises(c.ConfigurationError): candidate(value, {'action': 'save-group', 'revision': 0, 'group': second})

    def test_renamed_endpoint_invalidates_cached_resolution(self):
        value = config(); value['nodes'][0]['resolved'] = ['192.0.2.1']
        raw = node(address='new.example.invalid')
        result = candidate(value, {'action': 'save-node', 'revision': 0, 'node': raw})
        self.assertEqual(result['nodes'][0]['resolved'], [])

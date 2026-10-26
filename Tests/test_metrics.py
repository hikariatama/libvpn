import importlib.util
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('metrics', pathlib.Path(__file__).resolve().parents[1] / 'Resources/metrics.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MetricsTests(unittest.TestCase):
    def test_counters_use_link_row_once(self):
        output = '''Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll
utun8 1380 <Link#27> 10 0 1200 8 0 450 0
utun8 1380 10.0.0.1/32 10.0.0.1 10 - 1200 8 - 450 -
'''
        with patch.object(m, 'run', return_value=SimpleNamespace(stdout=output, stderr='', returncode=0)):
            self.assertEqual(m.counters('utun8'), (1200, 450))

    def test_failed_ping_is_a_loss_sample(self):
        with patch.object(m, 'run', return_value=SimpleNamespace(stdout='', stderr='', returncode=2)):
            self.assertEqual(m.ping('192.0.2.1'), (None, False))

    def test_inactive_path_does_not_probe(self):
        with patch.object(m, 'run') as run:
            self.assertEqual(m.ping('192.0.2.1', False), (None, False))
            run.assert_not_called()

    def test_total_counters_cover_all_active_physical_interfaces(self):
        output = '''en0: flags=8863<UP> mtu 1500\n\tstatus: active\nen1: flags=8863<UP> mtu 1500\n\tstatus: inactive\nen2: flags=8863<UP> mtu 1500\n\tstatus: active\n'''
        with patch.object(m, 'run', return_value=SimpleNamespace(stdout=output, stderr='', returncode=0)):
            self.assertEqual(m.physical_interfaces(), ['en0', 'en2'])
        with patch.object(m, 'counters', side_effect=[(100, 20), (300, 40)]):
            self.assertEqual(m.total_counters(['en0', 'en2']), (400, 60))

    def test_dynamic_server_probes_do_not_emit_addresses(self):
        import json
        nodes = [{'id': 'custom', 'name': 'Custom', 'flag': '', 'address': '192.0.2.1'}]
        with patch.object(m, 'ping', return_value=(12.0, True)) as ping, \
             patch.object(m, 'physical_interfaces', return_value=['en0']), \
             patch.object(m, 'total_counters', return_value=(500, 200)):
            value = m.snapshot(nodes)
        self.assertEqual(value['paths'][0]['id'], 'custom')
        self.assertEqual(value['rx'], 500)
        self.assertNotIn('192.0.2.1', json.dumps(value))
        ping.assert_called_once_with('192.0.2.1')

    def test_empty_registry_and_traffic_do_not_probe(self):
        with patch.object(m, 'ping') as ping, patch.object(m, 'physical_interfaces', return_value=[]):
            self.assertEqual(m.snapshot([], False)['paths'], [])
        ping.assert_not_called()

import copy
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'Resources'))
from configuration import empty_configuration


def node(identifier='sample', address='192.0.2.1'):
    return {'id': identifier, 'name': 'Example', 'flag': '🌐', 'country_code': '',
            'address': address, 'port': 443, 'protocol': 'vless', 'transport': 'tcp',
            'uuid': '00000000-0000-4000-8000-000000000001',
            'tls': {'enabled': True, 'server_name': 'example.invalid'}}


def group(identifier='custom'):
    return {'id': identifier, 'title': 'Custom', 'flag': '', 'enabled': True, 'nodes': ['sample'],
            'domains': ['example.invalid'], 'suffixes': [], 'cidrs': [], 'snapshots': []}


def config():
    value = empty_configuration(); value['nodes'] = [node()]; value['groups'] = [group()]
    value['fallback'].update(enabled=True, nodes=['sample'])
    return value

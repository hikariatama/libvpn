#!/usr/bin/python3
"""Generate sing-box/PF configuration and supervise the protected tunnel."""
import contextlib
import fcntl
import http.client
import ipaddress
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse
from configuration import ConfigurationError, normalize, require
from runtime_support import tls_context

HOME = pathlib.Path('/Library/Application Support/Fennec')
BASE = HOME / 'Engine'
BIN = BASE / 'sing-box'
CONFIG = BASE / 'configuration.json'
ASSETS = BASE / 'assets'
RUNTIME = BASE / 'runtime.json'
TOKEN = BASE / 'dns-token'
STATE = pathlib.Path('/var/run/local.fennec.engine.json')
LOG_DIR = pathlib.Path('/Library/Logs/Fennec')
PF_ANCHOR = 'local.fennec.vpn'
PF_FILE = pathlib.Path('/etc/pf.anchors/local.fennec.vpn')
PF_LOCK = pathlib.Path('/var/run/local.fennec.pf.lock')
OPERATION_LOCK = pathlib.Path('/var/run/local.fennec.operations.lock')
PF_REFERENCE = pathlib.Path('/var/run/local.fennec.pf-reference.json')
DNS_PROXY_PORT = 18100
REMOTE_TAG = 'remote-access'
REMOTE_MTU = 1280
REMOTE_KEEPALIVE = 25
REMOTE_PROBE_PORT = 9
REMOTE_PROBE_SECONDS = 3
TAILNET_TAG = 'tailnet'
TAILNET_DNS = 'tailnet-dns'
TAILNET_DIR = BASE / 'tailnet'
TAILNET_CONTROL = 'https://controlplane.tailscale.com'
TAILNET_PROBE = '100.100.100.100'
# The embedded Tailscale client uploads node logs to log.tailscale.com unless this is set.
ENGINE_ENVIRONMENT = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TS_NO_LOGS_NO_SUPPORT': 'true'}
DOMAIN_MATCHERS = (('domains', 'domain'), ('suffixes', 'domain_suffix'), ('keywords', 'domain_keyword'), ('regexes', 'domain_regex'))


def run(args, input_text=None, timeout=30):
    return subprocess.run([str(v) for v in args], input=input_text, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def atomic_json(path, value, mode=0o600):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def atomic_file(path, data, mode=0o600, validate=None):
    """Commit validated bytes durably without truncating the previous file."""
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data); output.flush(); os.fsync(output.fileno())
        if validate:
            validate(pathlib.Path(name))
        os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def write_pf_anchor(data):
    def validate(path):
        require(run(['/sbin/pfctl', '-n', '-a', PF_ANCHOR, '-f', path]).returncode == 0,
                'PF отклонил конфигурацию')
    atomic_file(PF_FILE, data, validate=validate)


def load_configuration():
    try:
        return normalize(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        raise ConfigurationError('Не удалось прочитать конфигурацию libvpn') from None


def literal(address):
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return None


def endpoint_ips(node):
    return [literal(node['address'])] if literal(node['address']) else node.get('resolved', [])


def recv_exact(connection, count):
    result = b''
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise OSError('Incomplete proxy response')
        result += chunk
    return result


def tunneled_lookup(name):
    """DoH over the authenticated loopback proxy; never use system DNS here."""
    token = TOKEN.read_text().strip().encode()
    values = []
    for kind in ('A', 'AAAA'):
        with socket.create_connection(('127.0.0.1', DNS_PROXY_PORT), timeout=5) as proxy:
            proxy.sendall(b'\x05\x01\x02')
            require(recv_exact(proxy, 2) == b'\x05\x02', 'Туннельный DNS недоступен')
            proxy.sendall(b'\x01\x04root' + bytes([len(token)]) + token)
            require(recv_exact(proxy, 2) == b'\x01\x00', 'Туннельный DNS недоступен')
            proxy.sendall(b'\x05\x01\x00\x01' + socket.inet_aton('1.1.1.1') + struct.pack('!H', 443))
            head = recv_exact(proxy, 4)
            require(head[:2] == b'\x05\x00', 'Туннельный DNS недоступен')
            size = {1: 4, 4: 16}.get(head[3])
            if head[3] == 3:
                size = recv_exact(proxy, 1)[0]
            require(size is not None, 'Некорректный ответ DNS-прокси')
            recv_exact(proxy, size + 2)
            with tls_context().wrap_socket(proxy, server_hostname='cloudflare-dns.com') as secure:
                target = '/dns-query?' + urllib.parse.urlencode({'name': name, 'type': kind})
                secure.sendall(('GET ' + target + ' HTTP/1.1\r\nHost: cloudflare-dns.com\r\nAccept: application/dns-json\r\nConnection: close\r\n\r\n').encode())
                response = http.client.HTTPResponse(secure); response.begin()
                require(response.status == 200, 'Туннельный DNS недоступен')
                body = response.read(65537)
                require(len(body) <= 65536, 'Слишком большой ответ DNS')
                data = json.loads(body)
                require(data.get('Status') == 0, 'DNS не разрешил имя сервера')
                values.extend(item['data'] for item in data.get('Answer', []) if item.get('type') in (1, 28))
    return sorted({str(ipaddress.ip_address(v)) for v in values})


def remote_active(config):
    return bool(config.get('remoteAccess', {}).get('enabled', False))


def tailnet_active(config):
    return bool(config.get('tailnet', {}).get('enabled', False))


def tailnet_state_directory(network):
    return TAILNET_DIR / network['stateId']


def tailnet_control(network):
    parts = urllib.parse.urlsplit(network['controlURL'] or TAILNET_CONTROL)
    return parts.hostname, parts.port or 443


def tailnet_reachable(config):
    """MagicDNS at 100.100.100.100 answers only after the node is logged in and has a network map."""
    token = TOKEN.read_text().strip().encode()
    query = struct.pack('!HHHHHH', 0x5a5a, 0x0100, 1, 0, 0, 0) + b'\x0dlibvpn-status\x07invalid\x00' + struct.pack('!HH', 1, 1)
    try:
        with socket.create_connection(('127.0.0.1', DNS_PROXY_PORT), timeout=REMOTE_PROBE_SECONDS) as proxy:
            proxy.settimeout(REMOTE_PROBE_SECONDS)
            proxy.sendall(b'\x05\x01\x02')
            if recv_exact(proxy, 2) != b'\x05\x02':
                return False
            proxy.sendall(b'\x01\x04root' + bytes([len(token)]) + token)
            if recv_exact(proxy, 2) != b'\x01\x00':
                return False
            proxy.sendall(b'\x05\x01\x00\x01' + ipaddress.ip_address(TAILNET_PROBE).packed + struct.pack('!H', 53))
            head = recv_exact(proxy, 4)
            if head[:2] != b'\x05\x00' or head[3] not in (1, 4):
                return False
            recv_exact(proxy, {1: 4, 4: 16}[head[3]] + 2)
            proxy.sendall(struct.pack('!H', len(query)) + query)
            size = struct.unpack('!H', recv_exact(proxy, 2))[0]
            answer = recv_exact(proxy, size)
            return size >= 12 and answer[:2] == b'\x5a\x5a' and answer[2] & 0x80 == 0x80
    except (OSError, struct.error):
        return False


def tailnet_registered(config):
    """Only checks that a node identity exists; the state file holds private keys and is never returned."""
    try:
        data = json.loads((tailnet_state_directory(config['tailnet']) / 'tailscaled.state').read_text())
        return isinstance(data, dict) and bool(data.get('_current-profile'))
    except (OSError, ValueError, KeyError):
        return False


def prune_tailnet(config):
    """Delete node identities that the committed configuration no longer uses."""
    if (BASE / 'transaction.json').exists() or not TAILNET_DIR.is_dir() or TAILNET_DIR.is_symlink():
        return
    keep = config.get('tailnet', {}).get('stateId')
    for path in TAILNET_DIR.iterdir():
        if path.name != keep and re.fullmatch(r'[0-9a-f]{16}', path.name) and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)


def idle(config):
    return config.get('bypass', False) or not any(g['enabled'] or g.get('killSwitch', False) for g in config['groups'] + [config['fallback']])


def wireguard_keys():
    result = run([BIN, 'generate', 'wg-keypair'])
    private = re.search(r'^PrivateKey:\s*(\S+)\s*$', result.stdout, re.M)
    public = re.search(r'^PublicKey:\s*(\S+)\s*$', result.stdout, re.M)
    require(result.returncode == 0 and private and public, 'Не удалось создать ключи WireGuard')
    return private.group(1), public.group(1)


def remote_reachable(config):
    """Proxy replies optimistically: a prompt close after it proves the peer answered; silence means no path."""
    remote = config['remoteAccess']
    token = TOKEN.read_text().strip().encode()
    address = ipaddress.ip_address(remote['peerAddress'])
    try:
        with socket.create_connection(('127.0.0.1', DNS_PROXY_PORT), timeout=REMOTE_PROBE_SECONDS) as proxy:
            proxy.settimeout(REMOTE_PROBE_SECONDS)
            proxy.sendall(b'\x05\x01\x02')
            if recv_exact(proxy, 2) != b'\x05\x02':
                return False
            proxy.sendall(b'\x01\x04root' + bytes([len(token)]) + token)
            if recv_exact(proxy, 2) != b'\x01\x00':
                return False
            proxy.sendall(b'\x05\x01\x00' + (b'\x01' if address.version == 4 else b'\x04') + address.packed + struct.pack('!H', REMOTE_PROBE_PORT))
            head = recv_exact(proxy, 4)
            size = {1: 4, 4: 16}.get(head[3])
            if head[:2] != b'\x05\x00' or size is None:
                return head[:2] == b'\x05\x05'
            recv_exact(proxy, size + 2)
            try:
                proxy.recv(1)
            except socket.timeout:
                return False
            return True
    except OSError:
        return False


def resolve_nodes(config, protected=False, refresh=False):
    remote = [config['remoteAccess']] if remote_active(config) else []
    for node in config['nodes'] + remote:
        if literal(node['address']):
            node['resolved'] = [literal(node['address'])]
            continue
        if not refresh and node.get('resolved'):
            continue
        try:
            if protected:
                addresses = tunneled_lookup(node['address'])
            else:
                addresses = sorted({v[4][0] for v in socket.getaddrinfo(node['address'], node['port'], type=socket.SOCK_STREAM)})
            require(bool(addresses) and len(addresses) <= 32, 'DNS не вернул адрес сервера')
            node['resolved'] = addresses
        except (OSError, ValueError, ssl.SSLError):
            raise ConfigurationError('Не удалось разрешить имя сервера. При включённой защите нужен работающий туннель; прямой DNS не используется.') from None
    network = config.get('tailnet')
    if network and network['enabled'] and not network['route'] and (refresh or not network.get('resolved')):
        # Protection lets a direct control connection reach only these addresses.
        host, port = tailnet_control(network)
        try:
            if literal(host):
                network['resolved'] = [literal(host)]
            elif protected:
                network['resolved'] = tunneled_lookup(host)[:32]
            else:
                network['resolved'] = sorted({v[4][0] for v in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})[:32]
        except (OSError, ValueError, ssl.SSLError, ConfigurationError):
            network['resolved'] = network.get('resolved', [])
    return config


def protected(config):
    return not config.get('bypass', False) and (config.get('killSwitch', False) or any(
        g.get('killSwitch', False) for g in config['groups'] + [config['fallback']]))


def host_prefix(address):
    return address + ('/128' if ':' in address else '/32')


def remote_endpoint(remote, finals):
    ips = endpoint_ips(remote)
    require(bool(ips), 'Имя сервера ещё не разрешено')
    endpoint = {'type': 'wireguard', 'tag': REMOTE_TAG, 'system': False, 'mtu': REMOTE_MTU,
                'address': [host_prefix(remote['tunnelAddress'])], 'private_key': remote['privateKey'],
                'peers': [{'address': ips[0], 'port': remote['port'], 'public_key': remote['serverPublicKey'],
                           'allowed_ips': [host_prefix(remote['peerAddress'])], 'persistent_keepalive_interval': REMOTE_KEEPALIVE}]}
    if remote['route']:
        endpoint['detour'] = finals[remote['route']]
    return endpoint


def tailnet_endpoint(network, finals):
    state = tailnet_state_directory(network)
    # Received files default to a path relative to the working directory, which is `/` under launchd.
    endpoint = {'type': 'tailscale', 'tag': TAILNET_TAG, 'state_directory': str(state), 'taildrop_directory': str(state / 'Taildrop'),
                'ephemeral': network['ephemeral'], 'accept_routes': network['acceptRoutes'], 'listen_port': network['listenPort']}
    for field, key in (('controlURL', 'control_url'), ('hostname', 'hostname'), ('authKey', 'auth_key')):
        if network[field]:
            endpoint[key] = network[field]
    # Only the coordination server connection can use a chain; peer traffic is WireGuard over UDP.
    if network['route']:
        endpoint['detour'] = finals[network['route']]
    return endpoint


def build_runtime(config, assets=None, token='test-only-token'):
    config = normalize(config)
    assets = pathlib.Path(assets or ASSETS)
    registry = {n['id']: n for n in config['nodes']}
    outbounds = [{'type': 'direct', 'tag': 'direct'}]
    finals = {}
    groups = config['groups'] + [config['fallback']]
    for group in groups:
        previous = None
        previous_node = None
        for index, node_id in enumerate(group['nodes']):
            node = registry[node_id]
            ips = endpoint_ips(node)
            require(bool(ips), 'Имя сервера ещё не разрешено')
            tag = 'chain_' + group['id'] + '_' + str(index)
            address = ips[0]
            if previous_node and node.get('private_network') and node.get('private_network') == previous_node.get('private_network'):
                address = node['private_address']
            outbound = {'type': 'vless', 'tag': tag, 'server': address, 'server_port': node['port'],
                        'uuid': node['uuid'], 'tls': dict(node['tls']), 'packet_encoding': node.get('packet_encoding', 'xudp'),
                        'multiplex': {'enabled': False}}
            if previous:
                outbound['detour'] = previous
            else:
                outbound['connect_timeout'] = '8s'
            outbounds.append(outbound)
            previous, previous_node = tag, node
        finals[group['id']] = previous
    network = config['tailnet'] if tailnet_active(config) else None
    for group in groups:
        if group.get('exit') == 'tailnet':
            finals[group['id']] = TAILNET_TAG if network else None
    def action(group):
        if config.get('bypass', False):
            return {'action': 'route', 'outbound': 'direct'}
        if group['enabled'] and finals.get(group['id']):
            result = {'action': 'route', 'outbound': finals[group['id']]}
            if group.get('overrideAddress'):
                result['override_address'] = group['overrideAddress']
            return result
        if group.get('killSwitch', False):
            return {'action': 'reject', 'method': 'default'}
        return {'action': 'route', 'outbound': 'direct'}
    # DNS uses a configured chain even when the fallback is disabled.
    dns_chain = next((finals[g['id']] for g in [config['fallback']] + config['groups']
                      if g['enabled'] and g.get('exit') != 'tailnet' and not config.get('bypass', False)), None)
    rules = [{'action': 'sniff'}]
    if network:
        # A query sent to a private resolver behind the tailnet (a corporate DNS reached through
        # an accepted subnet route, named in /etc/resolver) goes there unchanged; all other DNS
        # is answered here. A LAN resolver never enters the tunnel, so it is not affected.
        rules.append({'protocol': 'dns', 'ip_is_private': True, 'preferred_by': [TAILNET_TAG],
                      'action': 'route', 'outbound': TAILNET_TAG})
    rules.append({'protocol': 'dns', 'action': 'hijack-dns'})
    proxy_action = {'action': 'route', 'outbound': dns_chain} if dns_chain else {'action': 'reject'}
    remote = config.get('remoteAccess') if remote_active(config) else None
    if remote:
        # Only the configured loopback port is reachable through the tunnel.
        rules += [{'inbound': REMOTE_TAG, 'network': 'tcp', 'port': remote['localPort'], 'action': 'route', 'outbound': 'direct',
                   'override_address': '127.0.0.1', 'override_port': remote['localPort']},
                  {'inbound': REMOTE_TAG, 'action': 'reject'},
                  {'inbound': 'endpoint-dns', 'ip_cidr': [host_prefix(remote['peerAddress'])], 'action': 'route', 'outbound': REMOTE_TAG}]
    if network:
        # The endpoint hands connections to its own address over as 127.0.0.1:<port>, so every
        # port that is not listed must be refused or peers could reach any local service.
        for service in network['services']:
            rules.append({'inbound': TAILNET_TAG, 'network': service['protocol'], 'port': service['port'], 'action': 'route',
                          'outbound': 'direct', 'override_address': '127.0.0.1', 'override_port': service['localPort']})
        rules += [{'inbound': TAILNET_TAG, 'action': 'reject'},
                  {'inbound': 'endpoint-dns', 'ip_cidr': [host_prefix(TAILNET_PROBE)], 'action': 'route', 'outbound': TAILNET_TAG}]
    rules.append({'inbound': 'endpoint-dns', **proxy_action})
    ips = sorted({ip for n in config['nodes'] for ip in endpoint_ips(n)})
    if ips:
        rules.append({'ip_cidr': ips, 'action': 'route', 'outbound': 'direct'})
    if network:
        # Peer addresses, accepted subnet routes and MagicDNS names always use the tailnet.
        rules.append({'preferred_by': [TAILNET_TAG], 'action': 'route', 'outbound': TAILNET_TAG})
    rules.append({'ip_is_private': True, 'action': 'route', 'outbound': 'direct'})
    snapshots = set()
    for group in config['groups']:
        for source, field in DOMAIN_MATCHERS + (('cidrs', 'ip_cidr'),):
            values = group.get(source, [])
            if values:
                rules.append({field: values, **action(group)})
        digests = [item['digest'] for item in group.get('ruleSets', [])]
        if digests:
            rules.append({'rule_set': digests, **action(group)})
            snapshots.update(digests)
    # Explicit final catch-all also supports reject without a deprecated block outbound.
    rules.append(action(config['fallback']))
    dns = {'type': 'https', 'tag': 'resolver', 'server': '1.1.1.1', 'server_port': 443,
           'tls': {'enabled': True, 'server_name': 'cloudflare-dns.com'}}
    if dns_chain:
        dns['detour'] = dns_chain
    dns_rules = [{'preferred_by': [TAILNET_DNS], 'action': 'route', 'server': TAILNET_DNS}] if network else []
    if not config.get('bypass', False):
        for group in config['groups']:
            dns_action = {'action': 'reject'} if group.get('killSwitch', False) and not group['enabled'] else {'action': 'route', 'server': 'resolver'}
            for source, field in DOMAIN_MATCHERS:
                if group.get(source):
                    dns_rules.append({field: group[source], **dns_action})
            # GeoIP holds only addresses: a name cannot match it before resolution.
            digests = [item['digest'] for item in group.get('ruleSets', []) if item['kind'] != 'geoip']
            if digests:
                dns_rules.append({'rule_set': digests, **dns_action})
        fallback = config['fallback']
        if fallback.get('killSwitch', False) and not fallback['enabled']:
            dns_rules.append({'action': 'reject'})
    inbounds = [{'type': 'mixed', 'tag': 'endpoint-dns', 'listen': '127.0.0.1', 'listen_port': DNS_PROXY_PORT,
                 'users': [{'username': 'root', 'password': token}]}]
    # With no route to serve, remote access and the tailnet run without TUN so bypass stays truly direct.
    if not ((remote or network) and idle(config)):
        # gVisor terminates TCP inside sing-box. The default system stack hands it back
        # to the macOS kernel, and with another VPN's utun already up (Tunnelblick) every
        # TCP connection failed after an engine start while DNS still answered.
        inbounds.insert(0, {'type': 'tun', 'tag': 'tun-in', 'address': ['172.19.0.1/30', 'fdfe:dcba:9876::1/126'], 'mtu': 1400,
                            'stack': 'gvisor', 'auto_route': True, 'strict_route': True, 'dns_mode': 'hijack'})
    runtime = {'log': {'disabled': True},
               'dns': {'servers': [dns], 'rules': dns_rules, 'final': 'resolver', 'strategy': 'prefer_ipv4'},
               'inbounds': inbounds,
               'outbounds': outbounds,
               'route': {'auto_detect_interface': True, 'final': 'direct', 'rules': rules,
                         'rule_set': [{'type': 'local', 'tag': digest, 'format': 'binary',
                                       'path': str(assets / (digest + '.srs'))} for digest in sorted(snapshots)]}}
    endpoints = [remote_endpoint(remote, finals)] if remote else []
    if network:
        endpoints.append(tailnet_endpoint(network, finals))
        runtime['dns']['servers'].append({'type': 'tailscale', 'tag': TAILNET_DNS, 'endpoint': TAILNET_TAG})
        runtime['route']['default_domain_resolver'] = 'resolver'
    if endpoints:
        runtime['endpoints'] = endpoints
    return runtime


def build_pf_rules(config, tunnel=None):
    if not protected(config):
        return ''
    require(tunnel is None or __import__('re').fullmatch(r'utun[0-9]+', tunnel), 'Некорректный TUN интерфейс')
    lines = ['pass out quick on lo0 all']
    if tunnel:
        lines.append(f'pass out quick on {tunnel} all')
    for node in config['nodes']:
        for ip in endpoint_ips(node):
            family = 'inet6' if ':' in ip else 'inet'
            lines.append(f'pass out quick {family} proto tcp to {ip} port {node["port"]} keep state')
            lines.append(f'pass out quick {family} proto {"icmp6" if family == "inet6" else "icmp"} to {ip} keep state')
    if tailnet_active(config):
        network = config['tailnet']
        # WireGuard and STUN leave from the fixed tailnet port; relays over HTTPS stay blocked.
        lines.append(f'pass out quick proto udp from any port {network["listenPort"]} keep state')
        if not network['route']:
            port = tailnet_control(network)[1]
            for ip in network.get('resolved', []):
                lines.append(f'pass out quick {"inet6" if ":" in ip else "inet"} proto tcp to {ip} port {port} keep state')
    # LAN exceptions must not turn a router's resolver into an external DNS bypass.
    lines += ['block drop out quick proto { tcp, udp } to any port { 53, 853 }',
              'pass out quick inet to { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 }',
              'pass out quick inet6 to { fc00::/7, fe80::/10, ff02::/16 }',
              'pass out quick inet proto udp from port 68 to 255.255.255.255 port 67',
              'block drop out quick all' if not tunnel or config.get('killSwitch', False) else 'pass out all']
    return '\n'.join(lines) + '\n'


@contextlib.contextmanager
def pf_lock():
    fd = os.open(PF_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def configuration_lock():
    fd = os.open(OPERATION_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError('Другое изменение или обновление ещё выполняется. Повторите действие после его завершения.') from None
        yield
    finally:
        os.close(fd)


def ensure_pf_enabled():
    # Take at most one enable reference per boot; never disable another PF user's protection.
    marker = PF_REFERENCE
    boot = run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], timeout=5)
    require(boot.returncode == 0 and bool(boot.stdout.strip()), 'Не удалось определить поколение PF')
    state = run(['/sbin/pfctl', '-s', 'info'])
    require(state.returncode == 0, 'Не удалось проверить защиту PF')
    enabled = bool(re.search(r'^Status:\s+Enabled\b', state.stdout, re.M))
    try:
        if enabled and json.loads(marker.read_text()).get('boot') == boot.stdout.strip():
            return
    except (OSError, ValueError):
        pass
    result = run(['/sbin/pfctl', '-E'])
    state = run(['/sbin/pfctl', '-s', 'info'])
    require(result.returncode == 0 and state.returncode == 0 and re.search(r'^Status:\s+Enabled\b', state.stdout, re.M),
            'Не удалось включить защиту PF')
    atomic_json(marker, {'boot': boot.stdout.strip()})


def apply_kill_switch(config, tunnel=None, flush=False):
    rules = build_pf_rules(config, tunnel)
    with pf_lock():
        require(run(['/sbin/pfctl', '-n', '-a', PF_ANCHOR, '-f', '-'], rules).returncode == 0, 'PF отклонил конфигурацию')
        # Disk copy excludes ephemeral TUN names: boot starts with protection closed.
        write_pf_anchor(build_pf_rules(config).encode())
        require(run(['/sbin/pfctl', '-a', PF_ANCHOR, '-f', '-'], rules).returncode == 0, 'Не удалось применить PF')
        if protected(config):
            ensure_pf_enabled()
        if flush and protected(config):
            # Opting into a system-wide kill switch also invalidates existing direct connections.
            require(run(['/sbin/pfctl', '-F', 'states']).returncode == 0, 'Не удалось закрыть прежние прямые соединения')


def publish(state, revision, error=None, tunnel=None):
    atomic_json(STATE, {'state': state, 'revision': revision, 'error': error, 'tunnel': tunnel, 'updated': time.time()}, 0o644)


def read_state():
    try:
        value = json.loads(STATE.read_text())
        if time.time() - value.get('updated', 0) > 5:
            return {'state': 'error', 'error': 'Движок не обновляет состояние'}
        return value
    except (ValueError, OSError):
        return {'state': 'off'}


def interfaces():
    return set(run(['/sbin/ifconfig', '-l']).stdout.split())


def own_tunnel(before):
    candidates = []
    for name in interfaces() - before:
        if not re.fullmatch(r'utun[0-9]+', name):
            continue
        details = run(['/sbin/ifconfig', name], timeout=5)
        if details.returncode == 0 and re.search(r'\binet\s+172\.19\.0\.1\b', details.stdout):
            candidates.append(name)
    return candidates[0] if len(candidates) == 1 else None


def effective_guard(config):
    journal = BASE / 'transaction.json'
    if journal.is_file():
        try:
            return json.loads(journal.read_text())['guard']
        except (OSError, ValueError, KeyError):
            raise ConfigurationError('Журнал транзакции повреждён') from None
    return config


def serve():
    require(os.geteuid() == 0, 'Root required')
    stopping = threading.Event()
    child = None
    config = None
    revision = -1
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        config = load_configuration(); revision = config['revision']
        guard = effective_guard(config)
        apply_kill_switch(guard, flush=protected(guard))
        idle_state = 'bypass' if config.get('bypass', False) else 'off'
        tunless = idle(config)
        if tunless and not (remote_active(config) or tailnet_active(config)):
            while not stopping.is_set():
                publish(idle_state, revision)
                stopping.wait(1)
            return
        if tailnet_active(config):
            TAILNET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        runtime = build_runtime(config, token=TOKEN.read_text().strip())
        atomic_json(RUNTIME, runtime)
        require(run([BIN, 'check', '-c', RUNTIME]).returncode == 0, 'Конфигурация движка не прошла проверку')
        before = interfaces()
        child = subprocess.Popen([str(BIN), 'run', '-c', str(RUNTIME)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 cwd=str(BASE), env=ENGINE_ENVIRONMENT)
        tunnel = None
        deadline = time.monotonic() + 10
        while not stopping.wait(0.1):
            require(child.poll() is None, 'Сетевой движок завершился')
            if tunless:
                publish(idle_state, revision)
                continue
            if not tunnel:
                tunnel = own_tunnel(before)
                if tunnel:
                    apply_kill_switch(effective_guard(config), tunnel)
                require(tunnel is not None or time.monotonic() < deadline, 'TUN интерфейс не появился')
            publish('on' if tunnel else 'starting', revision, tunnel=tunnel)
    except Exception:
        publish('error', revision, 'Движок остановлен; проверьте конфигурацию и доступность серверов')
        # launchd retries after the guard is restored in finally.
    finally:
        if config:
            try:
                guard = effective_guard(config)
                apply_kill_switch(guard, flush=protected(guard))
            except Exception:
                pass  # Never clear the existing anchor after a protection failure.
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(3)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


if __name__ == '__main__':
    serve()

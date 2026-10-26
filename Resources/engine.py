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


def resolve_nodes(config, protected=False, refresh=False):
    for node in config['nodes']:
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
    return config


def protected(config):
    return not config.get('bypass', False) and (config.get('killSwitch', False) or any(
        g.get('killSwitch', False) for g in config['groups'] + [config['fallback']]))


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
    def action(group):
        if config.get('bypass', False):
            return {'action': 'route', 'outbound': 'direct'}
        if group['enabled']:
            return {'action': 'route', 'outbound': finals[group['id']]}
        if group.get('killSwitch', False):
            return {'action': 'reject', 'method': 'default'}
        return {'action': 'route', 'outbound': 'direct'}
    # DNS uses a configured chain even when the fallback is disabled.
    dns_chain = next((finals[g['id']] for g in [config['fallback']] + config['groups'] if g['enabled'] and not config.get('bypass', False)), None)
    rules = [{'action': 'sniff'}, {'protocol': 'dns', 'action': 'hijack-dns'}]
    proxy_action = {'action': 'route', 'outbound': dns_chain} if dns_chain else {'action': 'reject'}
    rules.append({'inbound': 'endpoint-dns', **proxy_action})
    ips = sorted({ip for n in config['nodes'] for ip in endpoint_ips(n)})
    if ips:
        rules.append({'ip_cidr': ips, 'action': 'route', 'outbound': 'direct'})
    rules.append({'ip_is_private': True, 'action': 'route', 'outbound': 'direct'})
    snapshots = set()
    for group in config['groups']:
        for source, field in (('domains', 'domain'), ('suffixes', 'domain_suffix'), ('cidrs', 'ip_cidr'), ('snapshots', 'rule_set')):
            values = group.get(source, [])
            if values:
                rules.append({field: values, **action(group)})
            if source == 'snapshots':
                snapshots.update(values)
    # Explicit final catch-all also supports reject without a deprecated block outbound.
    rules.append(action(config['fallback']))
    dns = {'type': 'https', 'tag': 'resolver', 'server': '1.1.1.1', 'server_port': 443,
           'tls': {'enabled': True, 'server_name': 'cloudflare-dns.com'}}
    if dns_chain:
        dns['detour'] = dns_chain
    dns_rules = []
    if not config.get('bypass', False):
        for group in config['groups']:
            dns_action = {'action': 'reject'} if group.get('killSwitch', False) and not group['enabled'] else {'action': 'route', 'server': 'resolver'}
            for source, field in (('domains', 'domain'), ('suffixes', 'domain_suffix'), ('snapshots', 'rule_set')):
                if group.get(source):
                    dns_rules.append({field: group[source], **dns_action})
        fallback = config['fallback']
        if fallback.get('killSwitch', False) and not fallback['enabled']:
            dns_rules.append({'action': 'reject'})
    return {'log': {'disabled': True},
            'dns': {'servers': [dns], 'rules': dns_rules, 'final': 'resolver', 'strategy': 'prefer_ipv4'},
            'inbounds': [{'type': 'tun', 'tag': 'tun-in', 'address': ['172.19.0.1/30', 'fdfe:dcba:9876::1/126'], 'mtu': 1400,
                          'auto_route': True, 'strict_route': True, 'dns_mode': 'hijack'},
                         {'type': 'mixed', 'tag': 'endpoint-dns', 'listen': '127.0.0.1', 'listen_port': DNS_PROXY_PORT,
                          'users': [{'username': 'root', 'password': token}]}],
            'outbounds': outbounds,
            'route': {'auto_detect_interface': True, 'final': 'direct', 'rules': rules,
                      'rule_set': [{'type': 'local', 'tag': digest, 'format': 'binary',
                                    'path': str(assets / (digest + '.srs'))} for digest in sorted(snapshots)]}}


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
        PF_FILE.write_text(build_pf_rules(config)); PF_FILE.chmod(0o600)
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
        apply_kill_switch(effective_guard(config), flush=protected(config))
        if config.get('bypass', False) or not any(g['enabled'] or g.get('killSwitch', False) for g in config['groups'] + [config['fallback']]):
            while not stopping.is_set():
                publish('bypass' if config.get('bypass', False) else 'off', revision)
                stopping.wait(1)
            return
        runtime = build_runtime(config, token=TOKEN.read_text().strip())
        atomic_json(RUNTIME, runtime)
        require(run([BIN, 'check', '-c', RUNTIME]).returncode == 0, 'Конфигурация движка не прошла проверку')
        before = interfaces()
        child = subprocess.Popen([str(BIN), 'run', '-c', str(RUNTIME)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tunnel = None
        deadline = time.monotonic() + 10
        while not stopping.wait(0.1):
            require(child.poll() is None, 'Сетевой движок завершился')
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
                apply_kill_switch(effective_guard(config))
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

"""Versioned, bounded configuration and VLESS import. No system side effects."""
import base64
import copy
import ipaddress
import re
import urllib.parse
import uuid

VERSION = 10
MAX_NODES = 256
MAX_GROUPS = 256
MAX_RULE_SETS = 256
MATCHERS = ('domains', 'suffixes', 'keywords', 'regexes', 'cidrs', 'ruleSets')
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}')
DIGEST = re.compile(r'[a-f0-9]{64}')
TAILNET_FIELDS = {'enabled', 'controlURL', 'hostname', 'authKey', 'ephemeral', 'acceptRoutes', 'route', 'listenPort', 'services', 'stateId'}
TAILNET_PORT = 41641
MAX_TAILNET_SERVICES = 64
STATE_ID = re.compile(r'[0-9a-f]{16}')
GEO_NAMES = {'geoip': re.compile(r'[a-z0-9][a-z0-9-]{0,31}'), 'geosite': re.compile(r'[a-z0-9][a-z0-9@!._-]{0,127}')}


class ConfigurationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ConfigurationError(message)


def text(value, label, limit=256, empty=False):
    require(isinstance(value, str) and len(value) <= limit and (empty or bool(value.strip()))
            and not any(ord(c) < 32 for c in value), 'Некорректное поле: ' + label)
    return value.strip()


def identifier(value):
    require(isinstance(value, str) and ID.fullmatch(value), 'Некорректный идентификатор')
    return value


def hostname(value):
    value = text(value, 'имя сервера', 253).rstrip('.')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        try:
            value = value.encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ConfigurationError('Некорректное имя сервера') from None
        require(all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
                    for p in value.split('.')), 'Некорректное имя сервера')
        return value


def empty_configuration():
    return {'schemaVersion': VERSION, 'revision': 0, 'nodes': [], 'groups': [],
            'fallback': {'id': 'fallback', 'title': 'Остальной трафик', 'flag': '🌐',
                         'enabled': False, 'nodes': [], 'killSwitch': False}, 'killSwitch': False, 'bypass': False}


def node(raw):
    require(isinstance(raw, dict), 'Некорректная запись сервера')
    allowed = {'id', 'name', 'flag', 'country_code', 'address', 'port', 'protocol',
               'transport', 'uuid', 'tls', 'resolved', 'private_address', 'private_network', 'packet_encoding'}
    require(not set(raw) - allowed, 'Неподдерживаемое поле сервера')
    result = copy.deepcopy(raw)
    result['id'] = identifier(raw.get('id'))
    result['name'] = text(raw.get('name', raw['id']), 'название сервера', 128)
    result['flag'] = text(raw.get('flag', ''), 'значок', 16, True)
    country = raw.get('country_code', '')
    require(isinstance(country, str) and (not country or re.fullmatch('[A-Za-z]{2}', country)), 'Некорректная страна')
    result['country_code'] = country.upper()
    require(raw.get('protocol') == 'vless' and raw.get('transport') == 'tcp', 'Поддерживается только VLESS/TCP/TLS')
    result['address'] = hostname(raw.get('address'))
    require(type(raw.get('port')) is int and 1 <= raw['port'] <= 65535, 'Некорректный порт')
    try:
        result['uuid'] = str(uuid.UUID(raw.get('uuid')))
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError('Некорректный UUID сервера') from None
    tls = raw.get('tls')
    require(isinstance(tls, dict) and tls.get('enabled') is True, 'Требуется TLS')
    require(not set(tls) - {'enabled', 'server_name', 'certificate_public_key_sha256', 'min_version', 'max_version'}, 'Неподдерживаемая настройка TLS')
    name = hostname(tls.get('server_name'))
    pins = tls.get('certificate_public_key_sha256', [])
    require(isinstance(pins, list) and len(pins) <= 1, 'Ожидается один TLS pin')
    for pin in pins:
        try:
            valid = isinstance(pin, str) and len(base64.b64decode(pin, validate=True)) == 32
        except (ValueError, TypeError):
            valid = False
        require(valid, 'Некорректный TLS pin')
    result['tls'] = {'enabled': True, 'server_name': name}
    for field in ('min_version', 'max_version'):
        if field in tls:
            require(tls[field] in ('1.2', '1.3'), 'Поддерживаются TLS 1.2 и 1.3')
            result['tls'][field] = tls[field]
    require(tls.get('min_version', '1.2') <= tls.get('max_version', '1.3'), 'Некорректный диапазон TLS')
    if pins:
        result['tls']['certificate_public_key_sha256'] = pins
    resolved = raw.get('resolved', [])
    require(isinstance(resolved, list) and len(resolved) <= 32, 'Некорректные адреса сервера')
    try:
        result['resolved'] = sorted({str(ipaddress.ip_address(v)) for v in resolved})
    except ValueError:
        raise ConfigurationError('Некорректный IP сервера') from None
    if 'private_address' in raw or 'private_network' in raw:
        try:
            result['private_address'] = str(ipaddress.ip_address(raw.get('private_address')))
        except ValueError:
            raise ConfigurationError('Некорректный private IP') from None
        result['private_network'] = identifier(raw.get('private_network'))
    require(raw.get('packet_encoding', 'xudp') in ('xudp', 'packetaddr'), 'Неподдерживаемое кодирование пакетов')
    return result


def wireguard_key(value):
    try:
        valid = isinstance(value, str) and len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, TypeError):
        valid = False
    require(valid, 'Некорректный ключ WireGuard')
    return value


def tunnel_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ConfigurationError('Некорректный адрес в туннеле') from None
    require(address.is_private and not address.is_loopback and not address.is_link_local and not address.is_multicast,
            'Адрес в туннеле должен быть частным')
    return str(address)


def remote_access(raw, groups):
    require(isinstance(raw, dict), 'Некорректные настройки удалённого доступа')
    fields = {'enabled', 'address', 'port', 'serverPublicKey', 'tunnelAddress', 'peerAddress', 'localPort', 'route', 'privateKey', 'publicKey'}
    require(not set(raw) - (fields | {'resolved'}) and not fields - set(raw), 'Некорректные настройки удалённого доступа')
    require(type(raw['enabled']) is bool, 'Некорректные настройки удалённого доступа')
    for field in ('port', 'localPort'):
        require(type(raw[field]) is int and 1 <= raw[field] <= 65535, 'Некорректный порт')
    tunnel, peer = tunnel_ip(raw['tunnelAddress']), tunnel_ip(raw['peerAddress'])
    require(tunnel != peer and ipaddress.ip_address(tunnel).version == ipaddress.ip_address(peer).version,
            'Адреса Mac и сервера в туннеле должны различаться и быть одного типа')
    require(isinstance(raw['route'], str), 'Некорректные настройки удалённого доступа')
    if raw['route']:
        chain = next((g for g in groups if g['id'] == raw['route']), None)
        require(chain is not None and bool(chain['nodes']), 'Маршрут для удалённого доступа должен существовать и иметь цепочку')
    resolved = raw.get('resolved', [])
    require(isinstance(resolved, list) and len(resolved) <= 32, 'Некорректные адреса сервера')
    try:
        resolved = sorted({str(ipaddress.ip_address(v)) for v in resolved})
    except ValueError:
        raise ConfigurationError('Некорректный IP сервера') from None
    return {'enabled': raw['enabled'], 'address': hostname(raw['address']), 'port': raw['port'],
            'serverPublicKey': wireguard_key(raw['serverPublicKey']),
            'tunnelAddress': tunnel, 'peerAddress': peer, 'localPort': raw['localPort'], 'route': raw['route'],
            'privateKey': wireguard_key(raw['privateKey']), 'publicKey': wireguard_key(raw['publicKey']),
            'resolved': resolved}


def control_url(value):
    message = 'Укажите HTTPS-адрес сервера управления без логина, пароля и параметров'
    require(isinstance(value, str) and len(value) <= 2048 and not any(ord(c) <= 32 for c in value), message)
    if not value:
        return value
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
        parts.port
    except ValueError:
        raise ConfigurationError(message) from None
    require(parts.scheme == 'https' and bool(host) and parts.username is None and parts.password is None
            and not parts.query and not parts.fragment, message)
    return value.rstrip('/')


def tailnet_service(raw):
    require(isinstance(raw, dict) and set(raw) == {'protocol', 'port', 'localPort'}, 'Некорректный открытый порт')
    require(raw['protocol'] in ('tcp', 'udp'), 'Некорректный протокол открытого порта')
    for field in ('port', 'localPort'):
        require(type(raw[field]) is int and 1 <= raw[field] <= 65535, 'Некорректный порт')
    return {'protocol': raw['protocol'], 'port': raw['port'], 'localPort': raw['localPort']}


def tailnet(raw, groups):
    message = 'Некорректные настройки tailnet'
    require(isinstance(raw, dict) and not set(raw) - (TAILNET_FIELDS | {'resolved'}) and not TAILNET_FIELDS - set(raw), message)
    for field in ('enabled', 'ephemeral', 'acceptRoutes'):
        require(type(raw[field]) is bool, message)
    name = raw['hostname']
    require(isinstance(name, str) and (not name or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name)),
            'Имя устройства: строчные латинские буквы, цифры и дефис, до 63 символов')
    key = raw['authKey']
    require(isinstance(key, str) and len(key) <= 512 and not any(ord(c) <= 32 or ord(c) > 126 for c in key), 'Некорректный ключ авторизации')
    require(type(raw['listenPort']) is int and 1024 <= raw['listenPort'] <= 65535, 'Порт tailnet должен быть от 1024 до 65535')
    require(isinstance(raw['stateId'], str) and STATE_ID.fullmatch(raw['stateId']), message)
    require(isinstance(raw['route'], str), message)
    if raw['route']:
        chain = next((g for g in groups if g['id'] == raw['route']), None)
        require(chain is not None and bool(chain['nodes']), 'Маршрут для подключения к tailnet должен существовать и иметь цепочку')
    services = raw['services']
    require(isinstance(services, list) and len(services) <= MAX_TAILNET_SERVICES, 'Слишком много открытых портов')
    services = [tailnet_service(v) for v in services]
    require(len({(v['protocol'], v['port']) for v in services}) == len(services), 'Открытый порт повторяется')
    resolved = raw.get('resolved', [])
    require(isinstance(resolved, list) and len(resolved) <= 32, message)
    try:
        resolved = sorted({str(ipaddress.ip_address(v)) for v in resolved})
    except ValueError:
        raise ConfigurationError(message) from None
    return {'enabled': raw['enabled'], 'controlURL': control_url(raw['controlURL']), 'hostname': name, 'authKey': key,
            'ephemeral': raw['ephemeral'], 'acceptRoutes': raw['acceptRoutes'], 'route': raw['route'],
            'listenPort': raw['listenPort'], 'services': services, 'stateId': raw['stateId'], 'resolved': resolved}


def rule_set_url(value):
    message = 'Укажите HTTPS-адрес набора правил без логина и пароля'
    require(isinstance(value, str) and len(value) <= 2048 and not any(ord(c) <= 32 for c in value), message)
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname
        parts.port
    except ValueError:
        raise ConfigurationError(message) from None
    require(parts.scheme == 'https' and bool(host) and parts.username is None and parts.password is None, message)
    return value


def rule_set(raw):
    require(isinstance(raw, dict) and not set(raw) - {'kind', 'name', 'digest', 'url'}, 'Некорректный набор правил')
    kind, digest = raw.get('kind'), raw.get('digest')
    require((kind == 'custom' or kind in GEO_NAMES) and isinstance(digest, str) and DIGEST.fullmatch(digest), 'Некорректный набор правил')
    result = {'kind': kind, 'digest': digest}
    if kind == 'custom':
        result['name'] = text(raw.get('name'), 'название набора правил', 128)
        if raw.get('url'):
            result['url'] = rule_set_url(raw['url'])
    else:
        name = raw.get('name')
        require(isinstance(name, str) and GEO_NAMES[kind].fullmatch(name.strip().lower()) and not raw.get('url'), 'Некорректный набор правил')
        result['name'] = name.strip().lower()
    return result


def route(raw, registry, fallback=False):
    require(isinstance(raw, dict), 'Некорректная группа')
    allowed = {'id', 'title', 'flag', 'enabled', 'nodes', 'killSwitch'} | (set() if fallback else set(MATCHERS) | {'exit', 'overrideAddress'})
    require(not set(raw) - allowed, 'Неподдерживаемое поле группы')
    result = {k: raw.get(k) for k in ('id', 'title', 'flag', 'enabled', 'nodes')}
    require(type(raw.get('killSwitch', False)) is bool, 'Некорректный kill switch маршрута')
    result['killSwitch'] = raw.get('killSwitch', False)
    result['id'] = 'fallback' if fallback else identifier(result['id'])
    require(fallback or result['id'] != 'fallback', 'Зарезервированный идентификатор')
    result['title'] = text(result['title'], 'название группы', 128)
    result['flag'] = text(result.get('flag') or '', 'значок', 16, True)
    require(type(result['enabled']) is bool, 'Некорректное состояние группы')
    ids = result['nodes']
    require(isinstance(ids, list) and len(ids) <= MAX_NODES and all(isinstance(i, str) and i in registry for i in ids), 'Неизвестный сервер цепочки')
    require(len(set(ids)) == len(ids), 'Сервер повторяется в цепочке')
    exit_type = raw.get('exit', 'chain')
    require(exit_type in ('chain', 'tailnet'), 'Некорректный выход маршрута')
    if exit_type == 'tailnet':
        require(not ids, 'Маршрут через tailnet не использует цепочку серверов')
        result['exit'] = 'tailnet'
    else:
        require(not result['enabled'] or bool(ids), 'Для включения нужна цепочка серверов')
    override = raw.get('overrideAddress', '')
    if override:
        try:
            address = ipaddress.ip_address(override if isinstance(override, str) else '')
        except ValueError:
            raise ConfigurationError('Некорректный адрес назначения') from None
        require(not address.is_unspecified and not address.is_multicast, 'Некорректный адрес назначения')
        result['overrideAddress'] = str(address)
    if not fallback:
        for field in MATCHERS:
            values = raw.get(field, [])
            require(isinstance(values, list) and len(values) <= 100000, 'Слишком большой список правил')
            if field == 'ruleSets':
                require(len(values) <= MAX_RULE_SETS, 'Слишком много наборов правил')
                unique = {}
                for item in map(rule_set, values):
                    unique.setdefault(item['digest'], item)
                result[field] = list(unique.values())
            elif field == 'keywords':
                result[field] = list(dict.fromkeys(text(v, 'ключевое слово', 253) for v in values))
            elif field == 'regexes':
                result[field] = list(dict.fromkeys(text(v, 'регулярное выражение', 1024) for v in values))
            elif field == 'cidrs':
                require(all(isinstance(v, str) for v in values), 'Ожидается текстовый IP/CIDR')
                try:
                    result[field] = list(dict.fromkeys(str(ipaddress.ip_network(v, strict=False)) for v in values))
                except (ValueError, TypeError):
                    raise ConfigurationError('Некорректный IP/CIDR') from None
            else:
                result[field] = list(dict.fromkeys(hostname(v) for v in values))
        require(any(result[f] for f in MATCHERS), 'Добавьте условие совпадения')
    return result


def migrate_group(group):
    # v9 referenced anonymous .srs digests. Each stays attached as a renamable custom set.
    if not isinstance(group, dict) or 'snapshots' not in group:
        return group
    result = dict(group)
    digests = result.pop('snapshots')
    require(isinstance(digests, list) and all(isinstance(v, str) and DIGEST.fullmatch(v) for v in digests), 'Некорректный снимок правил')
    result['ruleSets'] = [{'kind': 'custom', 'name': v[:12], 'digest': v} for v in digests]
    return result


def migrate(value):
    # Read-time only: the file stays v9 until the next committed change, so a helper rollback can still read it.
    if not isinstance(value, dict) or value.get('schemaVersion') != 9:
        return value
    result = dict(value, schemaVersion=VERSION)
    if isinstance(value.get('groups'), list):
        result['groups'] = [migrate_group(v) for v in value['groups']]
    return result


def normalize(value):
    value = migrate(value)
    require(isinstance(value, dict) and value.get('schemaVersion') == VERSION, 'Неподдерживаемая версия конфигурации')
    require(not set(value) - {'schemaVersion', 'revision', 'nodes', 'groups', 'fallback', 'killSwitch', 'bypass', 'remoteAccess', 'tailnet'}, 'Неизвестное поле конфигурации')
    require(type(value.get('revision')) is int and value['revision'] >= 0, 'Некорректная ревизия')
    require(type(value.get('killSwitch')) is bool, 'Некорректный kill switch')
    require(type(value.get('bypass', False)) is bool, 'Некорректный bypass')
    nodes = value.get('nodes')
    groups = value.get('groups')
    require(isinstance(nodes, list) and len(nodes) <= MAX_NODES, 'Слишком много серверов')
    require(isinstance(groups, list) and len(groups) <= MAX_GROUPS, 'Слишком много групп')
    nodes = [node(v) for v in nodes]
    registry = {v['id']: v for v in nodes}
    require(len(registry) == len(nodes), 'Повторяющийся идентификатор сервера')
    require(all(isinstance(v, dict) for v in groups), 'Некорректная группа')
    require(isinstance(value.get('fallback'), dict), 'Некорректная группа')
    groups = [route(dict(v, killSwitch=value['killSwitch'] or v.get('killSwitch', False)), registry) for v in groups]
    require(len({v['id'] for v in groups}) == len(groups), 'Повторяющийся идентификатор группы')
    result = {'schemaVersion': VERSION, 'revision': value['revision'], 'nodes': nodes, 'groups': groups,
              'fallback': route(dict(value.get('fallback') or {}, killSwitch=value['killSwitch'] or value['fallback'].get('killSwitch', False)), registry, True),
              'killSwitch': False, 'bypass': value.get('bypass', False)}
    if 'remoteAccess' in value:
        result['remoteAccess'] = remote_access(value['remoteAccess'], groups + [result['fallback']])
    if 'tailnet' in value:
        result['tailnet'] = tailnet(value['tailnet'], groups + [result['fallback']])
    require('tailnet' in result or not any(g.get('exit') == 'tailnet' for g in groups),
            'Маршрут через tailnet требует настроенного tailnet')
    return result


def public(value, editing=False):
    result = copy.deepcopy(value)
    if not editing:
        for group in result['groups']:
            for field in MATCHERS:
                group.pop(field, None)
    for item in result['nodes']:
        secret = item.pop('uuid')
        pins = item['tls'].pop('certificate_public_key_sha256', [])
        item.pop('resolved', None)
        item.pop('private_address', None)
        item.pop('private_network', None)
        if editing:
            item['hasUUID'] = bool(secret)
            item['hasPin'] = bool(pins)
        else:
            for key in ('address', 'port', 'tls'):
                item.pop(key, None)
    remote = result.get('remoteAccess')
    if remote:
        remote.pop('privateKey'); remote.pop('resolved', None)
        if not editing:
            result['remoteAccess'] = {k: remote[k] for k in ('enabled', 'route', 'localPort')}
    network = result.get('tailnet')
    if network:
        key = network.pop('authKey'); network.pop('stateId'); network.pop('resolved', None)
        if editing:
            network['hasAuthKey'] = bool(key)
        else:
            result['tailnet'] = {k: network[k] for k in ('enabled', 'route', 'controlURL', 'hostname')}
    return result


def import_vless(uri):
    require(isinstance(uri, str) and len(uri) <= 8192 and not any(ord(c) < 32 for c in uri), 'Некорректная VLESS-ссылка')
    try:
        parsed = urllib.parse.urlsplit(uri)
        require(parsed.scheme == 'vless' and parsed.password is None and parsed.path in ('', '/'), 'Ожидается VLESS/TCP/TLS-ссылка')
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        options = dict(pairs)
        require(len(options) == len(pairs), 'Повторяющиеся параметры ссылки')
        require(not set(options) - {'type', 'security', 'encryption', 'sni', 'serverName', 'pinSHA256'}, 'Ссылка содержит неподдерживаемые параметры')
        require(options.get('type', 'tcp') == 'tcp' and options.get('security') == 'tls'
                and options.get('encryption', 'none') == 'none', 'Поддерживается только VLESS/TCP/TLS')
        require(not ('sni' in options and 'serverName' in options), 'Неоднозначное имя TLS')
        address = parsed.hostname
        tls = {'enabled': True, 'server_name': options.get('sni') or options.get('serverName') or address}
        if 'pinSHA256' in options:
            tls['certificate_public_key_sha256'] = [options['pinSHA256']]
        return node({'id': uuid.uuid4().hex, 'name': urllib.parse.unquote(parsed.fragment) or 'Сервер',
                     'flag': '', 'country_code': '', 'address': address, 'port': parsed.port if parsed.port is not None else 443,
                     'protocol': 'vless', 'transport': 'tcp', 'uuid': parsed.username, 'tls': tls})
    except (ValueError, TypeError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError('Некорректная VLESS-ссылка') from None

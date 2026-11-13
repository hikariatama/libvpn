"""Versioned, bounded configuration and VLESS import. No system side effects."""
import base64
import copy
import ipaddress
import re
import urllib.parse
import uuid

VERSION = 9
MAX_NODES = 256
MAX_GROUPS = 256
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}')
DIGEST = re.compile(r'[a-f0-9]{64}')


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


def route(raw, registry, fallback=False):
    require(isinstance(raw, dict), 'Некорректная группа')
    allowed = {'id', 'title', 'flag', 'enabled', 'nodes', 'killSwitch'} | (set() if fallback else {'domains', 'suffixes', 'cidrs', 'snapshots'})
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
    require(not result['enabled'] or bool(ids), 'Для включения нужна цепочка серверов')
    if not fallback:
        for field in ('domains', 'suffixes', 'cidrs', 'snapshots'):
            values = raw.get(field, [])
            require(isinstance(values, list) and len(values) <= 100000, 'Слишком большой список правил')
            if field == 'snapshots':
                require(all(isinstance(v, str) and DIGEST.fullmatch(v) for v in values), 'Некорректный снимок правил')
                result[field] = list(dict.fromkeys(values))
            elif field == 'cidrs':
                require(all(isinstance(v, str) for v in values), 'Ожидается текстовый IP/CIDR')
                try:
                    result[field] = list(dict.fromkeys(str(ipaddress.ip_network(v, strict=False)) for v in values))
                except (ValueError, TypeError):
                    raise ConfigurationError('Некорректный IP/CIDR') from None
            else:
                result[field] = list(dict.fromkeys(hostname(v) for v in values))
        require(any(result[f] for f in ('domains', 'suffixes', 'cidrs', 'snapshots')), 'Добавьте условие совпадения')
    return result


def normalize(value):
    require(isinstance(value, dict) and value.get('schemaVersion') == VERSION, 'Неподдерживаемая версия конфигурации')
    require(not set(value) - {'schemaVersion', 'revision', 'nodes', 'groups', 'fallback', 'killSwitch', 'bypass'}, 'Неизвестное поле конфигурации')
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
    return {'schemaVersion': VERSION, 'revision': value['revision'], 'nodes': nodes, 'groups': groups,
            'fallback': route(dict(value.get('fallback') or {}, killSwitch=value['killSwitch'] or value['fallback'].get('killSwitch', False)), registry, True),
            'killSwitch': False, 'bypass': value.get('bypass', False)}


def public(value, editing=False):
    result = copy.deepcopy(value)
    if not editing:
        for group in result['groups']:
            for field in ('domains', 'suffixes', 'cidrs', 'snapshots'):
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

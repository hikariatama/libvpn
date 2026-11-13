"""Immutable .srs assets. Downloads run as the user; only the root helper stores them."""
import base64
import hashlib
import http.client
import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from configuration import ConfigurationError, DIGEST, route, require, rule_set
from profiles import MAX_ASSET, references
from runtime_support import tls_context
import engine

GEO_SOURCES = {'geoip': 'https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-{}.srs',
               'geosite': 'https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-{}.srs'}
SRS_MAGIC = b'SRS'
DOWNLOAD_SECONDS = 120
ORPHAN_SECONDS = 24 * 60 * 60
MAX_EXPANDED = 5000
LISTED_FIELDS = {'domain': 'domains', 'domain_suffix': 'suffixes', 'domain_keyword': 'keywords',
                 'domain_regex': 'regexes', 'ip_cidr': 'cidrs'}
NOT_A_RULE_SET = 'Файл не является набором правил sing-box'
NOT_LISTABLE = 'Набор содержит условия, которые нельзя показать списками. Он останется набором правил.'


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, target):
        require(urllib.parse.urlsplit(target).scheme == 'https', 'Укажите HTTPS-адрес набора правил без логина и пароля')
        return super().redirect_request(request, fp, code, message, headers, target)


def source_url(item):
    if item['kind'] == 'custom':
        return item['url']
    return GEO_SOURCES[item['kind']].format(urllib.parse.quote(item['name'], safe='@!._-'))


def download(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_context()), HTTPSOnly)
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    data = bytearray()
    try:
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'libvpn'}), timeout=30) as response:
            while chunk := response.read(65536):
                data.extend(chunk)
                require(len(data) <= MAX_ASSET, 'Набор правил слишком большой')
                require(time.monotonic() < deadline, 'Не удалось загрузить набор правил. Проверьте адрес и доступ к сети.')
    except urllib.error.HTTPError as error:
        require(error.code != 404, 'Набор правил не найден')
        raise ConfigurationError('Не удалось загрузить набор правил. Проверьте адрес и доступ к сети.') from None
    except (OSError, http.client.HTTPException):
        raise ConfigurationError('Не удалось загрузить набор правил. Проверьте адрес и доступ к сети.') from None
    return bytes(data)


def encode(data):
    return base64.b64encode(data).decode()


def prepare(source, client):
    """Bridge side: obtain bytes for one source and have the helper store them."""
    try:
        require(isinstance(source, dict) and not set(source) - {'kind', 'name', 'url', 'data'}, 'Некорректный набор правил')
        item = rule_set({'digest': '0' * 64, **{k: v for k, v in source.items() if k != 'data'}})
        if 'data' in source:
            require(item['kind'] == 'custom' and 'url' not in item and isinstance(source['data'], str), 'Некорректный набор правил')
            encoded = source['data']
        else:
            require(item['kind'] != 'custom' or 'url' in item, 'Некорректный набор правил')
            encoded = encode(download(source_url(item)))
        reply = client({'action': 'store-rule-set', 'data': encoded})
        if not reply.get('ok'):
            return reply
        return {'ok': True, 'ruleSet': dict(item, digest=reply['digest'])}
    except ConfigurationError as error:
        return {'ok': False, 'error': str(error)}


def refresh(client):
    """Bridge side: download every updatable source. A failed one keeps its stored copy."""
    current = client({'action': 'configuration'})
    if not current.get('ok'):
        return current
    configuration = current['configuration']
    updates, failed, seen = [], [], set()
    for group in configuration['groups']:
        for item in group.get('ruleSets', []):
            key = (item['kind'], item['name'], item.get('url'))
            if key in seen or (item['kind'] == 'custom' and 'url' not in item):
                continue
            seen.add(key)
            try:
                reply = client({'action': 'store-rule-set', 'data': encode(download(source_url(item)))})
            except ConfigurationError:
                reply = {}
            if reply.get('ok'):
                updates.append(dict(item, digest=reply['digest']))
            else:
                failed.append(item['name'])
    if updates:
        current = client({'action': 'update-rule-sets', 'revision': configuration['revision'], 'updates': updates})
        if not current.get('ok'):
            return current
    if failed:
        return {'ok': False, 'error': 'Не удалось обновить наборы: ' + ', '.join(failed), 'configuration': current['configuration']}
    return {'ok': True, 'configuration': current['configuration']}


def compiled(data, folder):
    path = folder / 'rules.srs'
    if data[:len(SRS_MAGIC)] == SRS_MAGIC:
        path.write_bytes(data)
    else:
        source = folder / 'rules.json'
        source.write_bytes(data)
        require(engine.run([engine.BIN, 'rule-set', 'compile', '--output', path, source]).returncode == 0 and path.is_file(), NOT_A_RULE_SET)
        require(path.stat().st_size <= MAX_ASSET, 'Набор правил слишком большой')
    require(engine.run([engine.BIN, 'rule-set', 'decompile', '--output', folder / 'check.json', path]).returncode == 0, NOT_A_RULE_SET)
    return path.read_bytes()


def store(encoded):
    """Helper side: accept a compiled set or a JSON source, keep it under its digest."""
    require(isinstance(encoded, str) and len(encoded) <= (MAX_ASSET + 2) // 3 * 4, 'Набор правил слишком большой')
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ConfigurationError('Некорректный набор правил') from None
    require(bool(data), NOT_A_RULE_SET)
    with tempfile.TemporaryDirectory(prefix='rules-', dir=engine.BASE) as folder:
        blob = compiled(data, pathlib.Path(folder))
    digest = hashlib.sha256(blob).hexdigest()
    engine.ASSETS.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = engine.ASSETS / (digest + '.srs')
    if path.is_symlink() or path.exists():
        require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Повреждён набор правил')
        os.utime(path)
        return digest
    descriptor, name = tempfile.mkstemp(prefix='.rules-', dir=engine.ASSETS)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(blob); output.flush(); os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)
    return digest


def listed(source):
    values = {field: [] for field in LISTED_FIELDS.values()}
    rules = source.get('rules') if isinstance(source, dict) else None
    require(isinstance(rules, list), NOT_LISTABLE)
    try:
        for rule in rules:
            # Other keys narrow a rule (ports, logic, inversion), so its lists alone would match more.
            require(isinstance(rule, dict) and not set(rule) - set(LISTED_FIELDS), NOT_LISTABLE)
            for key, value in rule.items():
                values[LISTED_FIELDS[key]].extend([value] if isinstance(value, str) else value)
        # A leading dot excludes the bare domain; editor suffixes always include it.
        require(not any(v.startswith('.') for v in values['suffixes']), NOT_LISTABLE)
        if sum(map(len, values.values())) > MAX_EXPANDED:
            raise OverflowError
        result = route({'id': 'listed', 'title': 'listed', 'flag': '', 'enabled': False, 'nodes': [], **values}, {})
    except OverflowError:
        raise ConfigurationError('Набор слишком большой, чтобы показать его списками. Он останется набором правил.') from None
    except (ConfigurationError, AttributeError, TypeError):
        raise ConfigurationError(NOT_LISTABLE) from None
    return {field: result[field] for field in LISTED_FIELDS.values()}


def contents(digest):
    """Helper side: return a stored set as editor lists when that loses no meaning."""
    require(isinstance(digest, str) and DIGEST.fullmatch(digest), 'Некорректный набор правил')
    path = engine.ASSETS / (digest + '.srs')
    require(not path.is_symlink() and path.is_file(), 'Набор правил недоступен')
    with tempfile.TemporaryDirectory(prefix='rules-', dir=engine.BASE) as folder:
        output = pathlib.Path(folder) / 'source.json'
        require(engine.run([engine.BIN, 'rule-set', 'decompile', '--output', output, path]).returncode == 0, 'Повреждён набор правил')
        return listed(json.loads(output.read_text()))


def prune(configuration):
    """Drop sets no route references, sparing recent ones an open editor may still save."""
    if (engine.BASE / 'transaction.json').exists():
        return
    keep = references(configuration)
    try:
        for path in engine.ASSETS.glob('*.srs'):
            if path.stem not in keep and time.time() - path.lstat().st_mtime > ORPHAN_SECONDS:
                path.unlink(missing_ok=True)
    except OSError:
        pass  # Leftover files only cost disk space; never fail a committed change.

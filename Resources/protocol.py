"""Bounded newline JSON framing and fixed helper request vocabulary."""
import contextlib
import json
import threading
import time
from configuration import ConfigurationError, require

MAX_BYTES = 2 * 1024 * 1024
TRANSFER_BYTES = 48 * 1024 * 1024
UPDATE_BYTES = 180 * 1024 * 1024
FRAMING_VERSION = 2
ACTIONS = {'status', 'configuration', 'save-node', 'delete-node', 'save-group',
           'delete-group', 'reorder-groups', 'set-enabled', 'set-kill-switch', 'set-bypass', 'set-chain', 'reconnect-route',
           'save-remote-access', 'set-remote-access', 'delete-remote-access', 'reconnect', 'refresh-endpoints', 'metrics', 'traffic', 'export-profile', 'preview-profile', 'import-profile', 'update-helper',
           'prepare-rule-set', 'refresh-rule-sets', 'store-rule-set', 'inspect-rule-set', 'update-rule-sets',
           'save-tailnet', 'set-tailnet', 'delete-tailnet'}
# Network access stays in the user's bridge process; the root helper only receives bytes.
BRIDGE_ACTIONS = {'prepare-rule-set', 'refresh-rule-sets'}
TRANSFER_ACTIONS = {'preview-profile', 'import-profile', 'prepare-rule-set', 'store-rule-set'}
FIELDS = {
    'update-helper': {'manifest', 'signature', 'archive'},
    'status': set(), 'configuration': set(), 'metrics': set(), 'traffic': set(),
    'reconnect': set(), 'refresh-endpoints': set(), 'export-profile': set(),
    'preview-profile': {'revision', 'archive'}, 'import-profile': {'revision', 'archive', 'digest'},
    'save-node': {'revision', 'node'}, 'delete-node': {'revision', 'id'},
    'save-group': {'revision', 'group'}, 'delete-group': {'revision', 'id'},
    'reorder-groups': {'revision', 'ids'}, 'set-enabled': {'revision', 'id', 'enabled'},
    'set-kill-switch': {'revision', 'id', 'enabled'},
    'reconnect-route': {'revision', 'id'},
    'set-bypass': {'revision', 'enabled'}, 'set-chain': {'revision', 'id', 'nodes'},
    'save-remote-access': {'revision', 'remote'},
    'save-tailnet': {'revision', 'tailnet'}, 'set-tailnet': {'revision', 'enabled'}, 'delete-tailnet': {'revision'}, 'set-remote-access': {'revision', 'enabled'}, 'delete-remote-access': {'revision'},
    'prepare-rule-set': {'source'}, 'refresh-rule-sets': set(), 'store-rule-set': {'data'},
    'inspect-rule-set': {'digest'}, 'update-rule-sets': {'revision', 'updates'},
}


def validate(value):
    require(isinstance(value, dict) and isinstance(value.get('action'), str), 'Некорректный запрос')
    action = value['action']
    require(action in ACTIONS and set(value) - {'language'} == FIELDS[action] | {'action'}, 'Неизвестная операция или поля запроса')
    require(value.get('language', 'ru') in ('en', 'ru'), 'Неподдерживаемый язык')
    require(len(encode(value)) <= request_limit(action), 'Запрос слишком большой')
    if 'revision' in value:
        require(type(value['revision']) is int and value['revision'] >= 0, 'Некорректная ревизия')
    if 'id' in value:
        from configuration import identifier
        identifier(value['id'])
    if 'enabled' in value:
        require(type(value['enabled']) is bool, 'Ожидается логическое значение')
    return value


def request_limit(action):
    return UPDATE_BYTES if action == 'update-helper' else TRANSFER_BYTES if action in TRANSFER_ACTIONS else MAX_BYTES


class RequestBudget:
    """Bound encoded in-flight bodies, including those waiting for dispatch."""
    def __init__(self):
        self.lock = threading.Lock()
        self.bytes = 0
        self.large = False

    @contextlib.contextmanager
    def reserve(self, count, large=False):
        with self.lock:
            require(self.bytes + count <= UPDATE_BYTES + 8 * MAX_BYTES and not (large and self.large),
                    'Другое изменение или обновление ещё выполняется. Повторите действие после его завершения.')
            self.bytes += count
            if large:
                self.large = True
        try:
            yield
        finally:
            with self.lock:
                self.bytes -= count
                if large:
                    self.large = False


@contextlib.contextmanager
def receive_request(connection, budget, context=None):
    with budget.reserve(MAX_BYTES):
        request = read_frame(connection.recv, MAX_BYTES)
        if isinstance(request, dict) and context is not None:
            context['language'] = request.get('language', 'ru')
        if isinstance(request, dict) and 'frameBytes' in request:
            require(set(request) - {'language'} == {'action', 'frameBytes', 'framing'}
                    and request['framing'] == FRAMING_VERSION
                    and request.get('language', 'ru') in ('en', 'ru')
                    and request['action'] in TRANSFER_ACTIONS | {'update-helper'}, 'Некорректный запрос')
            count = request['frameBytes']
            require(type(count) is int and MAX_BYTES < count <= request_limit(request['action']), 'Запрос слишком большой')
            with budget.reserve(count, large=True):
                # The sender waits for permission before transmitting the body.
                connection.sendall(encode({'ready': True}))
                body = read_frame(connection.recv, count, expected=count)
                require(isinstance(body, dict) and body.get('action') == request['action']
                        and body.get('language', 'ru') == request.get('language', 'ru'), 'Некорректный запрос')
                try:
                    yield validate(body)
                finally:
                    body = None
        else:
            yield validate(request)


def read_frame(reader, limit=MAX_BYTES, expected=None):
    data = bytearray()
    deadline = time.monotonic() + 8
    while True:
        require(time.monotonic() <= deadline, 'Истекло время чтения запроса')
        part = reader( min(65536, limit + 1 - len(data)))
        if not part:
            raise ConfigurationError('Незавершённый запрос')
        start = len(data)
        data.extend(part)
        require(len(data) <= limit, 'Запрос слишком большой')
        # Search only the new chunk: rescanning the whole buffer made large frames quadratic.
        if data.find(b'\n', start) != -1:
            require(expected is None or len(data) == expected, 'Некорректный запрос')
            line, rest = data.split(b'\n', 1)
            require(not rest.strip(), 'Ожидается один запрос')
            try:
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        require(key not in result, 'Повторяющееся поле JSON')
                        result[key] = value
                    return result
                return json.loads(line, object_pairs_hook=unique,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ConfigurationError('Некорректный JSON')))
            except (ValueError, UnicodeError, RecursionError):
                raise ConfigurationError('Некорректный JSON') from None


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()

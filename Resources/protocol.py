"""Bounded newline JSON framing and fixed helper request vocabulary."""
import json
import time
from configuration import ConfigurationError, require

MAX_BYTES = 2 * 1024 * 1024
TRANSFER_BYTES = 48 * 1024 * 1024
UPDATE_BYTES = 180 * 1024 * 1024
ACTIONS = {'status', 'configuration', 'save-node', 'delete-node', 'save-group',
           'delete-group', 'reorder-groups', 'set-enabled', 'set-kill-switch', 'set-bypass', 'set-chain', 'reconnect-route',
           'reconnect', 'refresh-endpoints', 'metrics', 'traffic', 'export-profile', 'preview-profile', 'import-profile', 'update-helper'}
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
}


def validate(value):
    require(isinstance(value, dict) and isinstance(value.get('action'), str), 'Некорректный запрос')
    action = value['action']
    require(action in ACTIONS and set(value) - {'language'} == FIELDS[action] | {'action'}, 'Неизвестная операция или поля запроса')
    require(value.get('language', 'ru') in ('en', 'ru'), 'Неподдерживаемый язык')
    require(len(encode(value)) <= (UPDATE_BYTES if action == 'update-helper' else TRANSFER_BYTES if action in ('preview-profile', 'import-profile') else MAX_BYTES), 'Запрос слишком большой')
    if 'revision' in value:
        require(type(value['revision']) is int and value['revision'] >= 0, 'Некорректная ревизия')
    if 'id' in value:
        from configuration import identifier
        identifier(value['id'])
    if 'enabled' in value:
        require(type(value['enabled']) is bool, 'Ожидается логическое значение')
    return value


def read_frame(reader, limit=MAX_BYTES):
    data = bytearray()
    deadline = time.monotonic() + 8
    while True:
        require(time.monotonic() <= deadline, 'Истекло время чтения запроса')
        part = reader( min(65536, limit + 1 - len(data)))
        if not part:
            raise ConfigurationError('Незавершённый запрос')
        data.extend(part)
        require(len(data) <= limit, 'Запрос слишком большой')
        if b'\n' in data:
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

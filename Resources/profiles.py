"""Portable, bounded configuration archives. Never extract caller paths."""
import base64
import hashlib
import io
import json
import pathlib
import zipfile
from configuration import ConfigurationError, normalize, require

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_CONTENT = 64 * 1024 * 1024
MAX_ASSET = 32 * 1024 * 1024


def references(config):
    return {digest for group in config['groups'] for digest in group.get('snapshots', [])}


def pack(configuration, assets):
    config = normalize(configuration)
    config['revision'] = 0
    for node in config['nodes']:
        node.pop('resolved', None)
    files = {'configuration.json': json.dumps({'format': 'fennec-profile', 'version': 1,
             'configuration': config}, ensure_ascii=False).encode()}
    require(len(files['configuration.json']) <= 4 * 1024 * 1024, 'Конфигурация слишком большая')
    for digest in references(config):
        path = pathlib.Path(assets) / (digest + '.srs')
        require(not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_ASSET, 'Набор правил недоступен')
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == digest, 'Повреждён набор правил')
        files['assets/' + digest + '.srs'] = data
    require(sum(map(len, files.values())) <= MAX_CONTENT, 'Профиль слишком большой')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items(): archive.writestr(name, data)
    require(len(output.getvalue()) <= MAX_ARCHIVE, 'Профиль слишком большой')
    return base64.b64encode(output.getvalue()).decode()


def unpack(encoded, revision):
    require(isinstance(encoded, str) and len(encoded) <= (MAX_ARCHIVE + 2) // 3 * 4, 'Профиль слишком большой')
    try:
        data = base64.b64decode(encoded, validate=True)
        require(len(data) <= MAX_ARCHIVE, 'Профиль слишком большой')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [v.filename for v in members]
            require(len(members) <= 1025 and len(set(names)) == len(names)
                    and sum(v.file_size for v in members) <= MAX_CONTENT, 'Некорректный размер профиля')
            require('configuration.json' in names, 'В профиле нет конфигурации')
            require(archive.getinfo('configuration.json').file_size <= 4 * 1024 * 1024, 'Конфигурация слишком большая')
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    require(key not in result, 'Повторяющееся поле профиля');result[key] = value
                return result
            value = json.loads(archive.read('configuration.json'), object_pairs_hook=unique)
            require(isinstance(value, dict) and set(value) == {'format', 'version', 'configuration'}
                    and value['format'] == 'fennec-profile' and type(value['version']) is int and value['version'] == 1,
                    'Неподдерживаемый формат профиля')
            config = normalize(value['configuration'])
            config['revision'] = revision
            for node in config['nodes']: node['resolved'] = []
            wanted = references(config)
            require(set(names) == {'configuration.json'} | {'assets/' + v + '.srs' for v in wanted}, 'Профиль содержит посторонние файлы')
            blobs = {}
            for digest in wanted:
                name = 'assets/' + digest + '.srs'
                require(archive.getinfo(name).file_size <= MAX_ASSET, 'Набор правил слишком большой')
                blob = archive.read(name)
                require(hashlib.sha256(blob).hexdigest() == digest, 'Повреждён набор правил')
                blobs[digest] = blob
        return config, blobs, hashlib.sha256(data).hexdigest()
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError):
        # Never include archive contents, server addresses or JSON excerpts.
        raise ConfigurationError('Не удалось прочитать профиль. Проверьте формат, размер и целостность файла.') from None


def summary(config, blobs, digest):
    return {'digest': digest, 'servers': len(config['nodes']), 'groups': len(config['groups']),
            'snapshots': len(blobs), 'bypass': config.get('bypass', False), 'killSwitch': any(g.get('killSwitch', False) for g in config['groups'] + [config['fallback']])}

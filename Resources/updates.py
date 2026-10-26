"""Signed, bounded helper updates. No caller-supplied commands or filesystem paths."""
import base64
import hashlib
import io
import json
import os
import pathlib
import platform
import plistlib
import shutil
import stat
import tempfile
import time
import zipfile
import engine
from configuration import ConfigurationError, require

ROOT = pathlib.Path('/Library/Application Support/Fennec Update')
STATUS = pathlib.Path('/var/run/local.fennec.update.json')
PLIST = pathlib.Path('/Library/LaunchDaemons/local.fennec.update.plist')
LABEL = 'system/local.fennec.update'
RESOURCES = pathlib.Path(__file__).resolve().parent
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_CONTENT = 512 * 1024 * 1024


def state():
    try:
        value = json.loads(STATUS.read_text())
        if value.get('state') == 'updating' and time.time() - value.get('started', 0) > 300:
            return {'state': 'failed', 'error': 'Обновление помощника не завершено. Используйте восстановление через меню.'}
        # Only fixed status fields cross the unprivileged boundary.
        return {key: value[key] for key in ('state', 'build', 'error') if key in value}
    except (OSError, ValueError, TypeError):
        return {}


def bundled_request():
    source = RESOURCES / 'HelperUpdate'
    require((source / 'helper.zip').stat().st_size <= MAX_ARCHIVE, 'Пакет обновления слишком большой')
    return dict(action='update-helper', manifest=(source / 'manifest.json').read_text(),
                signature=(source / 'signature.txt').read_text(),
                archive=base64.b64encode((source / 'helper.zip').read_bytes()).decode())


def verify(request, folder):
    require(isinstance(request['manifest'], str) and len(request['manifest']) < 4096
            and isinstance(request['signature'], str) and len(request['signature']) < 128,
            'Некорректная подпись обновления')
    manifest = folder / 'manifest.json';manifest.write_text(request['manifest'])
    signature = folder / 'signature.txt';signature.write_text(request['signature'])
    # Verification always uses the previously installed root-owned verifier/key.
    require(engine.run([RESOURCES / 'PackageSignature', 'verify', RESOURCES / 'update-public-key.txt', manifest, signature]).returncode == 0,
            'Подпись обновления не прошла проверку')
    value = json.loads(request['manifest'])
    require(set(value) == {'schemaVersion', 'build', 'sequence', 'architecture', 'sha256', 'size'} and value['schemaVersion'] == 1,
            'Неподдерживаемый пакет обновления')
    require(value['architecture'] == platform.machine(), 'Архитектура обновления не совпадает')
    require(type(value['sequence']) is int and type(value['size']) is int and 0 < value['size'] <= MAX_ARCHIVE
            and isinstance(value['build'], str) and len(value['build']) == 64
            and all(c in '0123456789abcdef' for c in value['build']), 'Некорректный пакет обновления')
    current = json.loads((RESOURCES / 'update-release.json').read_text())
    require(value['sequence'] > current['sequence'], 'Устаревший или уже установленный пакет обновления')
    require(isinstance(request['archive'], str) and len(request['archive']) <= (MAX_ARCHIVE * 4 // 3 + 8), 'Пакет обновления слишком большой')
    try:
        data = base64.b64decode(request['archive'], validate=True)
    except ValueError:
        raise ConfigurationError('Некорректный пакет обновления') from None
    require(len(data) == value['size'] and hashlib.sha256(data).hexdigest() == value['sha256'], 'Пакет обновления повреждён')
    return value, data


def extract(data, destination, release):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist();names = set()
        require(len(entries) <= 20000 and sum(item.file_size for item in entries) <= MAX_CONTENT, 'Пакет обновления слишком большой')
        for item in entries:
            path = pathlib.PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            require(item.filename not in names and not path.is_absolute() and '\\' not in item.filename
                    and all(part not in ('..', '.') for part in path.parts) and str(path) == item.filename
                    and len(item.filename) < 512 and stat.S_ISREG(mode), 'Недопустимый файл обновления')
            names.add(item.filename)
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as output:
                with archive.open(item) as source:
                    shutil.copyfileobj(source, output)
            target.chmod(0o755 if mode & 0o111 else 0o600)
    metadata = json.loads((destination / 'update-release.json').read_text())
    require(metadata == {key: release[key] for key in ('build', 'sequence', 'architecture')}, 'Версия пакета обновления не совпадает')
    require(json.loads((destination / 'helper-build.json').read_text())['build'] == release['build'], 'Версия помощника не совпадает')
    require((destination / 'update-public-key.txt').read_bytes() == (RESOURCES / 'update-public-key.txt').read_bytes(), 'Ключ обновления не совпадает')
    for name in ('Python/bin/python3', 'launcher.py', 'sing-box', 'PackageSignature'):
        require((destination / name).is_file(), 'Неполный пакет обновления')


def prepare(request):
    with engine.configuration_lock():
        require(state().get('state') != 'updating', 'Обновление помощника уже выполняется')
        require(not ROOT.is_symlink(), 'Недопустимый каталог обновления')
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700); ROOT.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix='verify-', dir=ROOT) as temporary:
            folder = pathlib.Path(temporary)
            release, data = verify(request, folder)
            staged = folder / 'Resources';staged.mkdir(mode=0o700)
            extract(data, staged, release)
            # No candidate code runs until its entire signed archive is verified.
            from runtime_support import command
            require(engine.run(command(staged, 'self-test')).returncode == 0, 'Пакет помощника не прошёл проверку')
            current = ROOT / 'Resources'
            if current.exists(): shutil.rmtree(current)
            os.rename(staged, current)
        engine.atomic_json(STATUS, dict(state='updating', build=release['build'], started=time.time()), 0o644)
        return {'ok': True, 'updateAccepted': True}


def start(uid):
    try:
        from runtime_support import command
        value = {'Label': 'local.fennec.update', 'ProgramArguments': command(ROOT / 'Resources', 'update', str(uid)),
                 'RunAtLoad': True, 'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}
        PLIST.write_bytes(plistlib.dumps(value));PLIST.chmod(0o644)
        engine.run(['/bin/launchctl', 'bootout', LABEL], timeout=15)
        require(engine.run(['/bin/launchctl', 'bootstrap', 'system', PLIST]).returncode == 0, 'Не удалось запустить обновление помощника')
    except Exception:
        engine.atomic_json(STATUS, dict(state='failed', error='Не удалось запустить обновление помощника'), 0o644)


def run(uid):
    require(os.geteuid() == 0 and uid > 0, 'Root updater requires console UID')
    try:
        installed = json.loads((engine.HOME / 'update-release.json').read_text())
        staged = json.loads((RESOURCES / 'update-release.json').read_text())
        require(staged['sequence'] > installed['sequence'] or staged == installed,
                'Устаревший или уже установленный пакет обновления')
        require((engine.HOME / 'update-public-key.txt').read_bytes() == (RESOURCES / 'update-public-key.txt').read_bytes(),
                'Ключ обновления не совпадает')
        import installation
        installation.execute(uid)
        from release import helper_build
        result = dict(state='done', build=helper_build())
        try:
            from bridge import diagnostic
            diagnostic('helper-update-completed')
        except Exception:
            pass  # A diagnostic write must not change an installation result.
    except Exception:
        result = dict(state='failed', error='Не удалось обновить помощник. Предыдущая установка сохранена в резервной копии; при неполадках используйте восстановление через меню.')
    PLIST.unlink(missing_ok=True)
    engine.atomic_json(STATUS, result, 0o644)

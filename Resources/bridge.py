#!/usr/bin/python3
"""UI JSON bridge and authenticated root helper. Never log request payloads."""
import copy
import hashlib
import json
import os
import pathlib
import socket
import sys
import threading
import time
import engine
import metrics
import protocol
import profiles
import updates
import localization
from release import helper_build
from configuration import ConfigurationError, VERSION, public, require
from store import RecoveryError, Transaction, candidate

SOCK = '/var/run/local.fennec.sock'
ENGINE_SERVICE = 'system/local.fennec.engine'
operation_lock = threading.Lock()
failed_recovery = None


def client(request):
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(180)
            connection.connect(SOCK); connection.sendall(protocol.encode(request))
            reply = protocol.read_frame(connection.recv, protocol.TRANSFER_BYTES)
            if request['action'] == 'status':
                if not reply.get('ok') and 'language' in request:
                    # Older helpers do not understand the optional locale field.
                    # Only the read-only handshake may drop it for compatibility.
                    return client({key: value for key, value in request.items() if key != 'language'})
                if reply.get('ok') and reply.get('version') != VERSION:
                    return {'ok': True, 'installed': True, 'requiresUpdate': True, 'state': 'off'}
                reply['installed'] = True
                reply['updateState'] = updates.state().get('state')
                if reply['updateState'] == 'failed' and not reply.get('error'):
                    reply['updateError'] = updates.state().get('error')
            return reply
    except (OSError, ValueError):
        if request['action'] == 'status':
            installed = pathlib.Path(SOCK).exists() or pathlib.Path('/Library/LaunchDaemons/local.fennec.helper.plist').exists()
            update = updates.state()
            return {'ok': True, 'installed': installed, 'requiresUpdate': installed, 'version': VERSION, 'state': 'off',
                    'updateState': update.get('state'), 'updateError': update.get('error')}
        return {'ok': False, 'error': 'Системный помощник недоступен'}


def diagnostic(event):
    # Fixed events only; raw exceptions and engine stderr can disclose addresses.
    engine.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = engine.LOG_DIR / 'diagnostics.jsonl'
    if path.exists() and path.stat().st_size > 1024 * 1024:
        os.replace(path, path.with_suffix('.previous.jsonl'))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a') as output:
        output.write(json.dumps({'at': time.time(), 'event': event}) + '\n')


def check_configuration(value):
    runtime = engine.build_runtime(value, token=engine.TOKEN.read_text().strip())
    with __import__('tempfile').TemporaryDirectory(prefix='check-', dir=engine.BASE) as folder:
        path = pathlib.Path(folder) / 'runtime.json'
        engine.atomic_json(path, runtime)
        require(engine.run([engine.BIN, 'check', '-c', path]).returncode == 0, 'Конфигурация не прошла проверку sing-box')
    require(engine.run(['/sbin/pfctl', '-n', '-a', engine.PF_ANCHOR, '-f', '-'], engine.build_pf_rules(value)).returncode == 0, 'Конфигурация не прошла проверку PF')


def stop_engine():
    # SIGTERM lets the old supervisor restore its guard before any new settings are applied.
    deadline = time.monotonic() + 15
    result = engine.run(['/bin/launchctl', 'bootout', ENGINE_SERVICE], timeout=15)
    require(result.returncode in (0, 3, 113), 'Не удалось остановить движок')
    # bootout can return before launchd finishes removing the job. A bootstrap
    # during that interval fails for both the candidate and its rollback.
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Не удалось остановить движок')
        result = engine.run(['/bin/launchctl', 'print', ENGINE_SERVICE], timeout=min(3, remaining))
        if result.returncode in (3, 113):
            return
        require(result.returncode == 0, 'Не удалось остановить движок')
        time.sleep(min(0.1, remaining))


def start_engine():
    engine.STATE.unlink(missing_ok=True)
    path = '/Library/LaunchDaemons/local.fennec.engine.plist'
    deadline = time.monotonic() + 15
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Не удалось запустить движок')
        result = engine.run(['/bin/launchctl', 'bootstrap', 'system', path], timeout=min(5, remaining))
        if result.returncode == 0:
            return
        # launchd can briefly reject re-registration even after print reports
        # no job. Retry only its transient I/O/busy/in-progress return codes.
        require(result.returncode in (5, 16, 36, 37), 'Не удалось запустить движок')
        time.sleep(min(0.1, remaining))


def ready(revision):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = engine.read_state()
        if state.get('revision') == revision and state.get('state') in ('on', 'off', 'blocked', 'bypass'):
            return
        time.sleep(0.1)
    raise ConfigurationError('Движок не подтвердил запуск')


def ready_helper(expected):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        reply = client({'action': 'status'})
        if reply.get('ok') and reply.get('installed') and reply.get('version') == VERSION and reply.get('helperBuild') == expected:
            return
        time.sleep(0.1)
    raise ConfigurationError('Помощник не подтвердил запуск')


def guard(config):
    state = engine.read_state()
    engine.apply_kill_switch(config, state.get('tunnel') if state.get('state') == 'on' else None, flush=engine.protected(config))


def transaction():
    return Transaction(check_configuration, stop_engine, guard, start_engine, ready, diagnostic)


def recover():
    transaction().recover()


def recover_if_needed(retry=False):
    # Called under operation_lock. A failed recovery must not restart the VPN on
    # every status/metrics poll. A new journal (or explicit reconnect) may retry.
    global failed_recovery
    path = engine.BASE / 'transaction.json'
    if not path.exists():
        failed_recovery = None
        return None
    stat = path.stat()
    identity = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    if not retry and failed_recovery == identity:
        return 'Не удалось восстановить предыдущее состояние. Защита сохранена. Восстановите помощник через меню.'
    try:
        recover()
        failed_recovery = None
        return None
    except RecoveryError as error:
        failed_recovery = identity
        return str(error)


def dispatch(request):
    protocol.validate(request)
    action = request['action']
    if action in ('metrics', 'traffic'):
        with operation_lock:
            current = engine.load_configuration()
        return {'ok': True, 'metrics': metrics.snapshot(current['nodes'] if action == 'metrics' else [], action == 'metrics')}
    with operation_lock:
        recovery_error = recover_if_needed(retry=action == 'reconnect')
        current = engine.load_configuration()
        if action == 'status':
            state = engine.read_state()
            return {'ok': True, 'installed': True, 'version': VERSION, 'state': 'blocked' if recovery_error else state.get('state', 'off'),
                    'error': recovery_error or state.get('error'), 'recoveryRequired': bool(recovery_error),
                    'configuration': public(current), 'helperBuild': helper_build(), 'signedUpdates': True}
        if action == 'configuration':
            return {'ok': True, 'configuration': public(current, editing=True)}
        if action == 'export-profile':
            return {'ok': True, 'archive': profiles.pack(current, engine.ASSETS)}
        if action == 'update-helper':
            return updates.prepare(request)
        require(not recovery_error, recovery_error)
        if action in ('preview-profile', 'import-profile'):
            require(request['revision'] == current['revision'], 'Настройки изменились. Откройте профиль заново.')
            new, blobs, digest = profiles.unpack(request['archive'], current['revision'] + 1)
            if action == 'preview-profile':
                return {'ok': True, 'profile': profiles.summary(new, blobs, digest)}
            require(request['digest'] == digest, 'Профиль изменился. Откройте файл заново.')
            engine.resolve_nodes(new, protected=engine.protected(current))
            created = []
            try:
                engine.ASSETS.mkdir(mode=0o700, parents=True, exist_ok=True)
                for key, data in blobs.items():
                    path = engine.ASSETS / (key + '.srs')
                    if path.exists() or path.is_symlink():
                        require(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == key, 'Повреждён набор правил')
                    else:
                        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        created.append(path)
                        with os.fdopen(fd, 'wb') as output: output.write(data);output.flush();os.fsync(output.fileno())
                transaction().apply(current, new)
            except Exception:
                if not (engine.BASE / 'transaction.json').exists():
                    for path in created: path.unlink(missing_ok=True)
                raise
            diagnostic('import-profile')
            return {'ok': True, 'configuration': public(new, editing=True)}
        if action in ('reconnect', 'refresh-endpoints'):
            new = copy.deepcopy(current)
            if action == 'refresh-endpoints':
                engine.resolve_nodes(new, protected=engine.protected(current), refresh=True)
                if new == current:
                    return {'ok': True, 'configuration': public(current, editing=True)}
            new['revision'] += 1
        else:
            new = candidate(current, request)
        engine.resolve_nodes(new, protected=engine.protected(current))
        transaction().apply(current, new)
        diagnostic(action)
        return {'ok': True, 'configuration': public(new, editing=True)}


def peer_uid(connection):
    import ctypes
    uid = ctypes.c_uint(); gid = ctypes.c_uint()
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.getpeereid(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) == 0, 'Не удалось проверить клиента')
    return uid.value


def handle(connection, uid):
    request = {};reply = {}
    try:
        require(peer_uid(connection) in (0, uid), 'Недопустимый пользователь')
        connection.settimeout(3)
        request = protocol.read_frame(connection.recv, protocol.UPDATE_BYTES)
        connection.settimeout(180)
        reply = dispatch(request)
    except ConfigurationError as error:
        reply = {'ok': False, 'error': str(error)}
    except Exception:
        reply = {'ok': False, 'error': 'Операция не завершена. Предыдущие настройки сохранены.'}
    try:
        language = request.get('language', 'ru') if isinstance(request, dict) else 'ru'
        connection.sendall(protocol.encode(localization.reply(reply, language)))
    except OSError:
        pass
    if reply.get('updateAccepted'):
        updates.start(uid)


def daemon(uid):
    require(os.geteuid() == 0 and uid > 0, 'Root helper requires a console UID')
    pathlib.Path(SOCK).unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(SOCK); os.chown(SOCK, uid, -1); os.chmod(SOCK, 0o600); server.listen(8)
        semaphore = threading.BoundedSemaphore(8)
        def serve_one(connection):
            try:
                with connection:
                    handle(connection, uid)
            finally:
                semaphore.release()
        def refresh():
            while True:
                time.sleep(300)
                try:
                    if engine.read_state().get('state') == 'on':
                        dispatch({'action': 'refresh-endpoints'})
                except Exception:
                    diagnostic('endpoint-refresh-failed')
        threading.Thread(target=refresh, daemon=True).start()
        while True:
            connection, _ = server.accept()
            if not semaphore.acquire(blocking=False):
                connection.close(); continue
            threading.Thread(target=serve_one, args=(connection,), daemon=True).start()


def main():
    if len(sys.argv) > 1:
        action = sys.argv[1]
        if action == 'daemon' and len(sys.argv) == 3:
            daemon(int(sys.argv[2])); return
        if action == 'install' and len(sys.argv) == 3:
            import installation
            try:
                result = installation.execute(int(sys.argv[2]))
            except Exception:
                result = {'ok': False, 'error': 'Не удалось обновить системный помощник. Предыдущая установка сохранена в закрытой резервной копии.'}
            print(json.dumps(result, ensure_ascii=False)); return
        raise SystemExit('Unknown command')
    try:
        request = protocol.read_frame(sys.stdin.buffer.read1, protocol.UPDATE_BYTES)
        if set(request) - {'language'} == {'action'} and request.get('action') == 'update-helper':
            request = dict(updates.bundled_request(), language=request.get('language', 'ru'))
        protocol.validate(request)
        print(json.dumps(localization.reply(client(request), request.get('language', 'ru')), ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Некорректный запрос'}))


if __name__ == '__main__':
    main()

"""Administrator-authorized installation and updates; never invoked by builds."""
import fcntl
import hashlib
import json
import os
import pathlib
import plistlib
import re
import secrets
import shutil
import tempfile
import time
import engine
from configuration import empty_configuration, normalize, require
from runtime_support import command as runtime_command
from release import helper_build

RESOURCE_DIR = pathlib.Path(__file__).resolve().parent
LABELS = ('local.fennec.engine', 'local.fennec.helper')
PLISTS = pathlib.Path('/Library/LaunchDaemons')
BACKUPS = pathlib.Path('/Library/Application Support/Fennec Backups')
PF_CONF = pathlib.Path('/etc/pf.conf')


def write_plist(path, value):
    path.write_bytes(plistlib.dumps(value)); path.chmod(0o644); os.chown(path, 0, 0)


def ensure_pf_anchor():
    path = PF_CONF
    original = path.read_text()
    marker = 'anchor "local.fennec.vpn"'
    lines = [line for line in original.splitlines() if line.strip() != marker
             and not re.match(r'\s*load anchor "local\.fennec\.vpn"', line)]
    index = next((i for i, line in enumerate(lines) if re.match(r'\s*(?:anchor|pass|block|antispoof)\b', line)), len(lines))
    lines.insert(index, marker)
    lines.append('load anchor "local.fennec.vpn" from "/etc/pf.anchors/local.fennec.vpn"')
    candidate = '\n'.join(lines) + '\n'
    fd, name = tempfile.mkstemp(prefix='fennec-pf.', dir='/private/tmp')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(candidate)
        require(engine.run(['/sbin/pfctl', '-n', '-f', name]).returncode == 0, 'PF configuration rejected')
        path.write_text(candidate); path.chmod(0o644)
        require(engine.run(['/sbin/pfctl', '-f', path]).returncode == 0, 'PF configuration failed')
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def execute(uid):
    require(os.geteuid() == 0 and uid > 0, 'Root installation requires console UID')
    with engine.configuration_lock():
        return install(uid)


def install(uid, initial_profile=None):
    # No credential input files in /tmp and no private data in the app bundle.
    require(not engine.HOME.is_symlink(), 'Installation directory must not be a symlink')
    assets = {}
    pending = None
    previous_revision = None
    if engine.CONFIG.exists():
        require(initial_profile is None, 'Existing configuration must be imported through the helper')
        config = engine.load_configuration()
        previous_revision = config['revision']
        journal = engine.BASE / 'transaction.json'
        if journal.exists():
            pending = json.loads(journal.read_text())
            config = normalize(pending['old'])
        for group in config['groups']:
            for digest in group.get('snapshots', []):
                assets[digest] = (engine.ASSETS / (digest + '.srs')).read_bytes()
    elif initial_profile is not None:
        config, assets = initial_profile
        config = normalize(config)
    else:
        require(not engine.BASE.exists() or not any(engine.BASE.iterdir()), 'Unknown saved configuration; existing files were not changed')
        config = empty_configuration()
    protection = pending['guard'] if pending else config
    engine.resolve_nodes(config, protected=engine.protected(protection))
    require((RESOURCE_DIR / 'sing-box').is_file(), 'Missing bundled runtime')
    require((RESOURCE_DIR / 'Python/bin/python3').is_file()
            and (RESOURCE_DIR / 'launcher.py').is_file(), 'Missing private helper runtime')
    # Preparing and validating the replacement has no effect on active services.
    with tempfile.TemporaryDirectory(prefix='fennec-install.', dir='/private/tmp') as folder:
        staged = pathlib.Path(folder) / 'Fennec'; staged.mkdir(mode=0o700)
        shutil.copytree(RESOURCE_DIR / 'Python', staged / 'Python')
        # Validate the copied interpreter before stopping or changing services.
        for source in RESOURCE_DIR.glob('*.py'):
            shutil.copy2(source, staged / source.name)
        if (RESOURCE_DIR / 'helper-build.json').is_file():
            shutil.copy2(RESOURCE_DIR / 'helper-build.json', staged / 'helper-build.json')
        for name in ('PackageSignature', 'update-public-key.txt', 'update-release.json', 'localizations.json'):
            if (RESOURCE_DIR / name).is_file():
                shutil.copy2(RESOURCE_DIR / name, staged / name)
        require(engine.run(runtime_command(staged, 'self-test')).returncode == 0, 'Private helper runtime rejected')
        base = staged / 'Engine'; base.mkdir(mode=0o700)
        (base / 'assets').mkdir(mode=0o700)
        for digest, data in assets.items():
            require(hashlib.sha256(data).hexdigest() == digest, 'Rule snapshot checksum mismatch')
            (base / 'assets' / (digest + '.srs')).write_bytes(data)
            (base / 'assets' / (digest + '.srs')).chmod(0o600)
        shutil.copy2(RESOURCE_DIR / 'sing-box', base / 'sing-box')
        (base / 'sing-box').chmod(0o755)
        secret = secrets.token_hex(24)
        (base / 'dns-token').write_text(secret); (base / 'dns-token').chmod(0o600)
        engine.atomic_json(base / 'configuration.json', config)
        if pending:
            engine.atomic_json(base / 'transaction.json', {'old': config, 'guard': protection})
        staged_runtime = engine.build_runtime(config, base / 'assets', secret)
        engine.atomic_json(base / 'runtime.json', staged_runtime)
        require(engine.run([base / 'sing-box', 'check', '-c', base / 'runtime.json']).returncode == 0, 'New runtime rejected')
        # Secure backups are outside HOME; never recurse into the backup itself.
        BACKUPS.mkdir(parents=True, exist_ok=True); BACKUPS.chmod(0o700)
        backup = BACKUPS / (str(time.time_ns()) + '-' + secrets.token_hex(4)); backup.mkdir(mode=0o700)
        existed = engine.HOME.exists()
        if existed:
            shutil.copytree(engine.HOME, backup / 'Home')
        saved_plists = {}
        for label in LABELS:
            path = PLISTS / (label + '.plist')
            if path.exists():
                saved_plists[label] = path.read_bytes()
                (backup / (label + '.plist')).write_bytes(saved_plists[label])
        pf_conf = PF_CONF
        old_pf = pf_conf.read_bytes()
        old_anchor = engine.PF_FILE.read_bytes() if engine.PF_FILE.exists() else b''
        (backup / 'pf.conf').write_bytes(old_pf); (backup / 'anchor').write_bytes(old_anchor)
        # Tighten every backup file, including files from earlier installations.
        for path in backup.rglob('*'):
            path.chmod(0o700 if path.is_dir() or path.stat().st_mode & 0o111 else 0o600)
        changed = False
        try:
            changed = True
            # Stop the helper before replacing its engine/runtime.
            for label in reversed(LABELS):
                engine.run(['/bin/launchctl', 'bootout', 'system/' + label], timeout=15)
            if engine.HOME.exists():
                shutil.rmtree(engine.HOME)
            shutil.copytree(staged, engine.HOME)
            engine.HOME.chmod(0o700)
            for path in engine.HOME.rglob('*'):
                os.chown(path, 0, 0)
            engine.PF_FILE.parent.mkdir(parents=True, exist_ok=True)
            engine.PF_FILE.write_text(engine.build_pf_rules(protection)); engine.PF_FILE.chmod(0o600)
            ensure_pf_anchor()
            engine.apply_kill_switch(protection, flush=engine.protected(protection))
            for label, role, extra in (('local.fennec.engine', 'engine', []), ('local.fennec.helper', 'bridge', ['daemon', str(uid)])):
                write_plist(PLISTS / (label + '.plist'), {'Label': label,
                    'ProgramArguments': runtime_command(engine.HOME, role, *extra),
                    'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
                    'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'})
            from bridge import start_engine, ready
            start_engine(); ready(config['revision'])
            if pending:
                from bridge import guard
                guard(config)
                (engine.BASE / 'transaction.json').unlink()
            require(engine.run(['/bin/launchctl', 'bootstrap', 'system', PLISTS / 'local.fennec.helper.plist']).returncode == 0, 'Helper failed to start')
            from bridge import ready_helper
            ready_helper(helper_build())
            return {'ok': True}
        except Exception:
            if changed:
                for label in LABELS:
                    engine.run(['/bin/launchctl', 'bootout', 'system/' + label], timeout=15)
                # Keep the stronger guard while restoring files/services.
                if engine.protected(protection):
                    engine.apply_kill_switch(protection)
                if engine.HOME.exists():
                    shutil.rmtree(engine.HOME)
                if existed:
                    shutil.copytree(backup / 'Home', engine.HOME)
                pf_conf.write_bytes(old_pf)
                engine.PF_FILE.write_bytes(old_anchor)
                engine.run(['/sbin/pfctl', '-f', pf_conf])
                for label in LABELS:
                    path = PLISTS / (label + '.plist')
                    if label in saved_plists:
                        path.write_bytes(saved_plists[label]); path.chmod(0o644)
                    else:
                        path.unlink(missing_ok=True)
                try:
                    for label in LABELS:
                        if label in saved_plists:
                            require(engine.run(['/bin/launchctl', 'bootstrap', 'system', PLISTS / (label + '.plist')]).returncode == 0, 'Restored service failed to launch')
                    if existed and 'local.fennec.engine' in saved_plists and (backup / 'Home/Engine/configuration.json').is_file():
                        ready(previous_revision)
                except Exception:
                    if engine.protected(protection):
                        engine.apply_kill_switch(protection)
                    raise RuntimeError('Restoration failed; protection retained and backup preserved') from None
            raise

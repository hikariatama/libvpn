#!/usr/bin/env python3
"""Build and verify local, architecture-specific distribution artifacts."""
import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from fetch_python import VERSION as PYTHON_VERSION, RELEASE as PYTHON_RELEASE

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs'
ARTWORK = ROOT / 'work/artwork'
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?')
ARCHITECTURES = ('arm64', 'x86_64')


def run(args):
    return subprocess.run([str(v) for v in args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def metadata(info=None, architecture=None):
    info = info or plistlib.loads((ROOT / 'Resources/Info.plist').read_bytes())
    version = info['CFBundleShortVersionString']
    architecture = architecture or platform.machine()
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('Bundle version must be safe for distribution filenames')
    if architecture not in ARCHITECTURES:
        raise ValueError('Unsupported build architecture')
    return {'version': version, 'architecture': architecture,
            'minimumMacOS': info['LSMinimumSystemVersion'],
            'stem': 'libvpn-' + version + '-' + architecture}


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def validate_zip(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
        for name in archive.namelist():
            parts = pathlib.PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or not parts.parts or parts.parts[0] != 'libvpn.app':
                raise ValueError('ZIP contains unexpected paths')
        info = plistlib.loads(archive.read('libvpn.app/Contents/Info.plist'))
        return info


def validate_app(app, expected):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if metadata(info, expected['architecture']) != expected:
        raise ValueError('Bundle metadata differs from distribution metadata')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
    for relative in ('Contents/MacOS/libvpn', 'Contents/Resources/sing-box', 'Contents/Resources/Python/bin/python3', 'Contents/Resources/PackageSignature'):
        archs = run(['/usr/bin/lipo', '-archs', app / relative]).stdout.decode().split()
        if archs != [expected['architecture']]:
            raise ValueError('Unexpected Mach-O architecture: ' + relative)
        run(['/usr/bin/codesign', '--verify', '--strict', app / relative])
    if not (app / 'Contents/Resources/libvpn.icns').is_file():
        raise ValueError('Application icon is missing')
    if not all((app / ('Contents/Resources/libvpn-wordmark-' + mode + '.pdf')).is_file() for mode in ('dark', 'light')):
        raise ValueError('Application wordmark is missing')
    validate_private_runtime(app / 'Contents/Resources', expected['architecture'])
    resources = app / 'Contents/Resources'
    package = resources / 'HelperUpdate'
    if package.exists():
        run([resources / 'PackageSignature', 'verify', resources / 'update-public-key.txt', package / 'manifest.json', package / 'signature.txt'])
        manifest = json.loads((package / 'manifest.json').read_text())
        if manifest['sha256'] != checksum(package / 'helper.zip') or manifest['size'] != (package / 'helper.zip').stat().st_size:
            raise ValueError('Signed helper archive checksum mismatch')
        release = json.loads((resources / 'update-release.json').read_text())
        if any(manifest[key] != release[key] for key in ('build', 'sequence', 'architecture')):
            raise ValueError('Signed helper archive version mismatch')


def validate_private_runtime(resources, architecture):
    runtime = resources / 'Python'
    details = json.loads((runtime / 'fennec-runtime.json').read_text())
    if (details['version'], details['release'], details['architecture']) != (PYTHON_VERSION, PYTHON_RELEASE, architecture):
        raise ValueError('Unexpected private Python runtime')
    if not (runtime / 'PYTHON.json').is_file() or not any((runtime / 'licenses').glob('LICENSE.*')):
        raise ValueError('Python dependency notices are missing')
    python = runtime / 'bin/python3'
    dependencies = run(['/usr/bin/otool', '-L', python]).stdout.decode().splitlines()[1:]
    if not dependencies or any(not line.strip().startswith(('/usr/lib/', '/System/Library/')) for line in dependencies):
        raise ValueError('Python links to a non-system external library')
    # Exercise the shipped interpreter after relocation, with no developer tools
    # on PATH and hostile user Python settings. This never starts a helper/app.
    with tempfile.TemporaryDirectory(prefix='fennec-runtime-check.', dir='/private/tmp') as folder:
        root = pathlib.Path(folder)
        poison = root / 'sitecustomize.py'
        marker = root / 'injected'
        poison.write_text('open(' + repr(str(marker)) + ', "w").write("injected")\n')
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': folder,
               'PYTHONHOME': folder, 'PYTHONPATH': folder, 'PYTHONSTARTUP': str(poison),
               'SSL_CERT_FILE': str(poison), 'SSL_CERT_DIR': folder}
        result = subprocess.run([str(python), '-I', '-S', '-B', str(resources / 'launcher.py'), 'self-test'],
                                cwd=folder, env=env, check=True, timeout=20, capture_output=True, text=True)
        value = json.loads(result.stdout)
        if value != {'ok': True, 'pythonVersion': PYTHON_VERSION, 'isolated': True} or marker.exists():
            raise ValueError('Private runtime self-test failed')
        if any(resources.rglob('__pycache__')):
            raise ValueError('Runtime wrote bytecode into the sealed bundle')


@contextlib.contextmanager
def mounted_image(image):
    with tempfile.TemporaryDirectory(prefix='fennec-dmg-check.', dir='/private/tmp') as folder:
        mount = pathlib.Path(folder) / 'volume'; mount.mkdir()
        response = plistlib.loads(run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen',
                                      '-mountpoint', mount, '-plist', image]).stdout)
        device = next(v['dev-entry'] for v in response['system-entities'] if v.get('mount-point') == str(mount))
        try:
            yield mount
        finally:
            for attempt in range(3):
                try:
                    run(['/usr/bin/hdiutil', 'detach', device])
                    break
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        # Only this read-only, temporary verification image can be forced.
                        run(['/usr/bin/hdiutil', 'detach', '-force', device])
                    else:
                        time.sleep(0.3)


def validate_layout(volume):
    from ds_store import DSStore
    if (volume / 'Applications').is_symlink() is False or os.readlink(volume / 'Applications') != '/Applications':
        raise ValueError('Applications drop target is missing')
    visible = {p.name for p in volume.iterdir() if not p.name.startswith('.')}
    if visible != {'libvpn.app', 'Applications'}:
        raise ValueError('Unexpected visible DMG contents')
    with DSStore.open(str(volume / '.DS_Store'), 'r') as store:
        window = store['.']['bwsp']; icons = store['.']['icvp']
        if window['WindowBounds'] != '{{160, 160}, {654, 444}}':
            raise ValueError('Incorrect Finder window size')
        if any(window.get(k, False) for k in ('ShowStatusBar', 'ShowTabView', 'ShowToolbar', 'ShowPathbar', 'ShowSidebar', 'ContainerShowSidebar', 'PreviewPaneVisibility')):
            raise ValueError('Unexpected Finder window chrome')
        if icons['backgroundType'] != 2 or not icons['backgroundImageAlias'] or icons['iconSize'] != 140:
            raise ValueError('Finder artwork or icon size is missing')
        if icons['textSize'] != 12 or not icons['labelOnBottom']:
            raise ValueError('Incorrect Finder labels')
        if icons['arrangeBy'] != 'none' or icons['scrollPositionX'] != 0 or icons['scrollPositionY'] != 0 or store['.']['icvl'] != (b'type', b'icnv'):
            raise ValueError('Finder must open in a fixed, unscrolled icon view')
        for name, position in {'libvpn.app': (197, 195), 'Applications': (473, 195)}.items():
            if tuple(store[name]['Iloc']) != position:
                raise ValueError('Incorrect Finder icon position')
    if not (volume / '.background.tiff').is_file():
        raise ValueError('DMG background is missing')
    run([ARTWORK / 'artwork', '--verify-background', volume / '.background.tiff', ROOT / 'Tools/Artwork/libvpn-dmg-banner.png'])


def validate_dmg(path, expected):
    run(['/usr/bin/hdiutil', 'verify', path])
    with mounted_image(path) as volume:
        validate_app(volume / 'libvpn.app', expected)
        validate_layout(volume)


def record(expected, artifacts):
    commit = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    revision = commit.stdout.strip() if commit.returncode == 0 else None
    files = [{'name': p.name, 'sha256': checksum(p), 'bytes': p.stat().st_size} for p in artifacts]
    value = dict(expected, schemaVersion=1, signing='ad-hoc', notarized=False, sourceCommit=revision,
                 embeddedPython={'version': PYTHON_VERSION, 'release': PYTHON_RELEASE}, files=files)
    target = OUTPUT / (expected['stem'] + '.json')
    target.write_text(json.dumps(value, indent=2) + '\n')
    sums = OUTPUT / (expected['stem'] + '.sha256')
    sums.write_text(''.join(v['sha256'] + '  ' + v['name'] + '\n' for v in files))
    return target


def package(archive):
    from dmgbuild import build_dmg
    info = validate_zip(archive)
    expected = metadata(info)
    if archive.name != expected['stem'] + '.zip':
        raise ValueError('ZIP filename does not match bundle metadata')
    with tempfile.TemporaryDirectory(prefix='fennec-package.', dir='/private/tmp') as folder:
        stage = pathlib.Path(folder)
        run(['/usr/bin/ditto', '-x', '-k', archive, stage])
        app = stage / 'libvpn.app'
        run(['/usr/bin/xattr', '-cr', app])
        validate_app(app, expected)
        image = stage / (expected['stem'] + '.dmg')
        build_dmg(str(image), 'libvpn', settings_file=str(ROOT / 'Tools/dmg_settings.py'),
                  defines={'app': str(app), 'artwork': str(ARTWORK)})
        validate_dmg(image, expected)
        destination = OUTPUT / image.name
        # os.replace keeps the app/DMG inputs outside iCloud until fully verified.
        import shutil
        shutil.copyfile(image, destination.with_suffix('.dmg.tmp'))
        os.replace(destination.with_suffix('.dmg.tmp'), destination)
        result = record(expected, [archive, destination])
        verify(result, require_dmg=True)
        print('Packaged ' + destination.name)


def verify(manifest, require_dmg=False):
    value = json.loads(manifest.read_text())
    expected = {k: value[k] for k in ('version', 'architecture', 'minimumMacOS', 'stem')}
    if expected != metadata(architecture=value['architecture']):
        raise ValueError('Manifest does not describe this source version')
    extensions = set()
    for item in value['files']:
        name = item['name']
        suffix = pathlib.Path(name).suffix
        if name != expected['stem'] + suffix or suffix not in ('.zip', '.dmg') or suffix in extensions:
            raise ValueError('Unexpected distribution filename')
        extensions.add(suffix)
        path = manifest.parent / name
        if path.stat().st_size != item['bytes'] or checksum(path) != item['sha256']:
            raise ValueError('Distribution checksum mismatch: ' + name)
        if suffix == '.dmg':
            validate_dmg(path, expected)
        else:
            info = validate_zip(path)
            if metadata(info, expected['architecture']) != expected:
                raise ValueError('ZIP metadata mismatch')
            with tempfile.TemporaryDirectory(prefix='fennec-zip-check.', dir='/private/tmp') as folder:
                run(['/usr/bin/ditto', '-x', '-k', path, folder])
                run(['/usr/bin/xattr', '-cr', pathlib.Path(folder) / 'libvpn.app'])
                validate_app(pathlib.Path(folder) / 'libvpn.app', expected)
    if '.zip' not in extensions or (require_dmg and '.dmg' not in extensions):
        raise ValueError('Distribution is incomplete')
    sums = manifest.with_suffix('.sha256').read_text()
    if sums != ''.join(v['sha256'] + '  ' + v['name'] + '\n' for v in value['files']):
        raise ValueError('Checksum sidecar does not match manifest')
    print('Verified ' + expected['stem'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stem', 'metadata', 'package', 'verify', 'record-zip'))
    parser.add_argument('path', nargs='?', type=pathlib.Path)
    parser.add_argument('--github-output', type=pathlib.Path)
    parser.add_argument('--require-dmg', action='store_true')
    args = parser.parse_args()
    if args.action in ('stem', 'metadata'):
        value = metadata()
        print(value['stem'] if args.action == 'stem' else json.dumps(value))
        if args.github_output:
            with args.github_output.open('a') as output:
                for key in ('stem', 'version', 'architecture'):
                    output.write(key + '=' + value[key] + '\n')
    elif args.path is None:
        parser.error('This action requires a file path')
    elif args.action == 'package':
        package(args.path.resolve())
    elif args.action == 'record-zip':
        validate_zip(args.path); record(metadata(), [args.path.resolve()])
    else:
        verify(args.path.resolve(), args.require_dmg)


if __name__ == '__main__':
    main()

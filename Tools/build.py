#!/usr/bin/python3
"""Native build entry point; all staging is outside File Provider directories."""
import argparse
import hashlib
import json
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from distribution import ARTWORK, OUTPUT, ROOT, metadata, record, run, validate_app
from fetch_python import prepare as prepare_python
from helper_package import create as create_helper_package


def build(app_only=False):
    if sys.platform != 'darwin':
        raise SystemExit('libvpn requires macOS and Apple Command Line Tools.')
    os.chdir(ROOT)
    value = metadata()
    runtime = ROOT / 'work/runtime-v9'
    scratch = ROOT / 'work/build'
    scratch.mkdir(parents=True, exist_ok=True); OUTPUT.mkdir(exist_ok=True); ARTWORK.mkdir(parents=True, exist_ok=True)
    subprocess.run(['/usr/bin/python3', 'Resources/fetch_runtime.py', str(runtime)], check=True)
    with tempfile.TemporaryDirectory(prefix='fennec-build.', dir='/private/tmp') as folder:
        stage = pathlib.Path(folder)
        artwork_tool = stage / 'artwork'
        run(['xcrun', 'swiftc', '-O', '-parse-as-library', '-swift-version', '5', 'Tools/Artwork.swift', '-o', artwork_tool, '-framework', 'Cocoa'])
        run([artwork_tool, ARTWORK, value['version'], value['architecture']])
        shutil.copyfile(artwork_tool, ARTWORK / 'artwork')
        (ARTWORK / 'artwork').chmod(0o755)
        run(['/usr/bin/iconutil', '-c', 'icns', '-o', ARTWORK / 'libvpn.icns', ARTWORK / 'libvpn.iconset'])
        app = stage / 'libvpn.app'
        binary = app / 'Contents/MacOS'; binary.mkdir(parents=True)
        resources = app / 'Contents/Resources'; resources.mkdir()
        prepare_python(ROOT / 'work/python-runtime', resources / 'Python')
        target = value['architecture'] + '-apple-macos' + value['minimumMacOS']
        sources = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'Source').glob('*.swift'))
        run(['xcrun', 'swiftc', '-O', '-swift-version', '5', '-target', target] + sources +
            ['-o', binary / 'libvpn', '-framework', 'Cocoa', '-framework', 'SwiftUI', '-framework', 'ServiceManagement'])
        run(['xcrun', 'swiftc', '-O', '-parse-as-library', '-target', target, 'Tools/PackageSignature.swift', '-o', resources / 'PackageSignature'])
        helper_digest = hashlib.sha256()
        for source in sorted((ROOT / 'Resources').glob('*.py')):
            if source.name != 'fetch_runtime.py': helper_digest.update(source.name.encode() + source.read_bytes())
        helper_digest.update((ROOT / 'Resources/fetch_runtime.py').read_bytes())
        helper_digest.update((ROOT / 'Tools/fetch_python.py').read_bytes())
        for path in (ROOT / 'Tools/PackageSignature.swift', ROOT / 'Resources/update-public-key.txt', ROOT / 'Resources/localizations.json'):
            helper_digest.update(path.read_bytes())
        identity = helper_digest.hexdigest()
        info = plistlib.loads((ROOT / 'Resources/Info.plist').read_bytes())
        info['FennecHelperBuild'] = identity
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        (resources / 'helper-build.json').write_text(json.dumps({'build': identity}) + '\n')
        (resources / 'update-release.json').write_text(json.dumps(dict(build=identity, sequence=time.time_ns(), architecture=value['architecture'])) + '\n')
        for name in ('update-public-key.txt', 'localizations.json'):
            shutil.copy2(ROOT / 'Resources' / name, resources / name)
        for path in (ROOT / 'Resources').glob('*.lproj'):
            shutil.copytree(path, resources / path.name)
        for source in (ROOT / 'Resources').glob('*.py'):
            if source.name == 'fetch_runtime.py':
                continue
            shutil.copy2(source, resources / source.name)
        for source in (ARTWORK / 'libvpn-wordmark-dark.pdf', ARTWORK / 'libvpn-wordmark-light.pdf', ROOT / 'THIRD_PARTY_NOTICES.md', ROOT / 'LICENSE', ARTWORK / 'libvpn.icns', runtime / 'sing-box'):
            shutil.copy2(source, resources / source.name)
        notices = resources / 'third-party'; notices.mkdir()
        shutil.copy2(runtime / 'sing-box-LICENSE', notices / 'sing-box-LICENSE')
        shutil.copy2(ROOT / 'Resources/third-party/GPL-3.0.txt', notices / 'GPL-3.0.txt')
        run(['/usr/bin/xattr', '-cr', app])
        run(['/usr/bin/codesign', '--force', '--sign', '-', resources / 'Python/bin/python3'])
        run(['/usr/bin/codesign', '--force', '--sign', '-', resources / 'sing-box'])
        run(['/usr/bin/codesign', '--force', '--sign', '-', resources / 'PackageSignature'])
        private_key = os.environ.get('FENNEC_SIGNING_KEY_FILE')
        if private_key:
            create_helper_package(resources, private_key, resources / 'HelperUpdate')
        elif not os.environ.get('CI'):
            local_key = pathlib.Path.home() / '.config/fennec/signing/release.key'
            if local_key.is_file(): create_helper_package(resources, local_key, resources / 'HelperUpdate')
        elif os.environ.get('GITHUB_REF', '').startswith('refs/tags/'):
            raise SystemExit('A signed release requires FENNEC_SIGNING_KEY_FILE')
        run(['/usr/bin/codesign', '--force', '--sign', '-', app])
        validate_app(app, value)
        archive = stage / (value['stem'] + '.zip')
        run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', app, archive])
        destination = OUTPUT / archive.name
        shutil.copyfile(archive, destination.with_suffix('.zip.tmp'))
        os.replace(destination.with_suffix('.zip.tmp'), destination)
        working_app = OUTPUT / 'libvpn.app'
        if working_app.exists():
            shutil.move(str(working_app), str(scratch / ('previous.' + str(time.time_ns()) + '.app')))
        shutil.copytree(app, working_app)
        run(['/usr/bin/xattr', '-cr', working_app])
        try:
            validate_app(working_app, value)
        except subprocess.CalledProcessError:
            print('File Provider modified the working app metadata; use the verified distribution ZIP.', file=sys.stderr)
    if app_only:
        record(value, [destination])
    else:
        subprocess.run([str(ROOT / 'Tools/package.sh'), str(destination)], check=True)
    print('Built ' + value['stem'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-only', action='store_true', help='Build the app and ZIP without installing DMG build dependencies')
    args = parser.parse_args()
    try:
        build(args.app_only)
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode)


if __name__ == '__main__':
    main()

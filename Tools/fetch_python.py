"""Prepare a checksum-pinned, private CPython runtime, never a system install."""
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

VERSION = '3.14.8'
RELEASE = '20261013'
ARCHIVES = {
    'arm64': ('aarch64', '994adadc202c5df0b4570192cc9a70e3f2c131e512f4a125c56efa8562f44b28',
              '5806ddb9d95d73796c87e1aeb45bf7ec984d08ae9eb94f9020436052b32ce498'),
    'x86_64': ('x86_64', 'f0137337ecf6234b30c9c955219dae2d71e1a00387c4d204d6c6929e24bf9a90',
               '193223f1380c93f62dda1d2b1f08ac46c0de5a57e51ab9946598d5d88e10742b'),
}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def download(cache, name, expected):
    path = cache / name
    if not path.exists() or digest(path) != expected:
        temporary = path.with_suffix('.download')
        url = 'https://github.com/astral-sh/python-build-standalone/releases/download/' + RELEASE + '/' + name.replace('+', '%2B')
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if digest(temporary) != expected:
            temporary.unlink()
            raise ValueError('Python archive checksum mismatch')
        os.replace(temporary, path)
    return path


def safe_member(member):
    parts = pathlib.PurePosixPath(member.name)
    if parts.is_absolute() or '..' in parts.parts or not parts.parts or parts.parts[0] != 'python':
        raise ValueError('Unsafe Python archive path')
    if not (member.isdir() or member.isfile() or member.issym()):
        raise ValueError('Unsupported Python archive entry')
    if member.issym():
        # Even unused links must not escape the pinned distribution.
        target = pathlib.PurePosixPath(member.linkname)
        resolved = os.path.normpath(str(parts.parent / target))
        if target.is_absolute() or not resolved.startswith('python/'):
            raise ValueError('Unsafe Python archive link')


def prepare(cache, destination):
    machine = platform.machine()
    if machine not in ARCHIVES:
        raise ValueError('Unsupported Python architecture')
    target, install_hash, full_hash = ARCHIVES[machine]
    prefix = 'cpython-' + VERSION + '+' + RELEASE + '-' + target + '-apple-darwin-'
    cache.mkdir(parents=True, exist_ok=True)
    archive = download(cache, prefix + 'install_only_stripped.tar.gz', install_hash)
    full = download(cache, prefix + 'pgo+lto-full.tar.zst', full_hash)
    # Extract only files needed to run the stdlib helper. No pip, headers, tools,
    # tkinter, user packages or symlinks are shipped in the app.
    with tarfile.open(archive) as package:
        members = package.getmembers()
        for member in members:
            safe_member(member)
        for member in members:
            if not member.isfile():
                continue
            parts = pathlib.PurePosixPath(member.name)
            relative = None
            if member.name == 'python/bin/python3.14':
                relative = pathlib.Path('bin/python3')
            elif member.name.startswith('python/lib/python3.14/'):
                tail = parts.parts[3:]
                if not tail or tail[0] in ('site-packages', 'ensurepip', 'idlelib', 'tkinter', 'test') or any(p == '__pycache__' for p in tail):
                    continue
                relative = pathlib.Path(*parts.parts[1:])
            if relative:
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(package.extractfile(member).read())
                output.chmod(0o755 if relative == pathlib.Path('bin/python3') else 0o644)
    python = destination / 'bin/python3'
    # The bundled Python supplies zstd support; the build does not need Homebrew
    # or a separate decompressor to preserve the upstream dependency licenses.
    script = pathlib.Path(__file__).with_name('python_notices.py')
    subprocess.run([str(python), '-I', '-S', '-B', str(script), str(full), str(destination)], check=True)
    (destination / 'fennec-runtime.json').write_text(json.dumps({
        'version': VERSION, 'release': RELEASE, 'architecture': machine,
        'archiveSHA256': install_hash, 'metadataArchiveSHA256': full_hash,
    }, indent=2) + '\n')
    print('Prepared private CPython ' + VERSION + ' (' + machine + ')')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: fetch_python.py CACHE DESTINATION')
    prepare(pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve())

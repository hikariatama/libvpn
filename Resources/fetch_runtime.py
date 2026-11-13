#!/usr/bin/python3
"""Fetch the pinned sing-box runtime for a build."""
import hashlib
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request


VERSION = '1.14.1'
ARCHIVES = {
    'arm64': ('sing-box-1.14.1-darwin-arm64.tar.gz', 'b9024642ef7b4848252df5469b7f60ef3c18bb5e217a16a0934f0174f8ad11b4'),
    'x86_64': ('sing-box-1.14.1-darwin-amd64.tar.gz', 'b34381b047106fe84895df14f7aaae06f3182130b728006944deb0d59d8590c3'),
}


def download(url, target):
    temporary = target.with_suffix(target.suffix + '.download')
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
        shutil.copyfileobj(response, output)
    os.replace(temporary, target)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: fetch_runtime.py OUTPUT_DIRECTORY')
    output = pathlib.Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    machine = platform.machine()
    if machine not in ARCHIVES:
        raise SystemExit(f'Unsupported macOS architecture: {machine}')
    archive_name, expected = ARCHIVES[machine]
    archive = output / archive_name
    if not archive.exists() or digest(archive) != expected:
        download(f'https://github.com/SagerNet/sing-box/releases/download/v{VERSION}/{archive_name}', archive)
    if digest(archive) != expected:
        raise SystemExit(f'Checksum mismatch for {archive_name}')
    binary = output / 'sing-box'
    with tarfile.open(archive) as package:
        member = next((item for item in package.getmembers() if item.name.endswith('/sing-box')), None)
        if member is None or not member.isfile():
            raise SystemExit('sing-box executable is missing from archive')
        member.name = 'sing-box'
        # The archive is pinned by SHA-256 and member.name is replaced with a
        # fixed basename before extraction (Python 3.9 has no filter= API).
        package.extract(member, output)
        license_member = next((item for item in package.getmembers() if item.name.endswith('/LICENSE') and item.isfile()), None)
        if license_member is None:
            raise SystemExit('Upstream license is missing from archive')
        license_file = package.extractfile(license_member)
        (output / 'sing-box-LICENSE').write_bytes(license_file.read())
    binary.chmod(0o755)
    version = subprocess.run([str(binary), 'version'], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    if version != f'sing-box version {VERSION}':
        raise SystemExit(f'Unexpected runtime: {version}')
    print(f'Prepared sing-box {VERSION} in {output}')


if __name__ == '__main__':
    main()

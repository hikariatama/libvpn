#!/usr/bin/env python3
"""Prepare manifest-only signing inputs and attach detached signatures without a key."""
import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile
import distribution as d
from helper_package import manifest_bytes, manifest_for


def prepare(app, output):
    expected = d.metadata()
    d.validate_app(app, expected)
    resources = app / 'Contents/Resources'
    if (resources / 'HelperUpdate').exists():
        raise ValueError('Signing input must come from an unsigned helper build')
    manifest = manifest_for(resources, resources / 'HelperInstall.zip')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'manifest.json').write_bytes(manifest_bytes(manifest))


def attach(resources, signed):
    # The signed archive is the already-built installation payload. Never rebuild
    # its runtime, sequence or manifest after the protected signer has approved it.
    archive = resources / 'HelperInstall.zip'
    raw = (signed / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest != manifest_for(resources, archive) or raw != manifest_bytes(manifest):
        raise ValueError('Detached signature does not describe the exact built helper')
    d.run([resources / 'PackageSignature', 'verify', resources / 'update-public-key.txt',
           signed / 'manifest.json', signed / 'signature.txt'])
    package = resources / 'HelperUpdate'
    package.mkdir()
    shutil.copyfile(archive, package / 'helper.zip')
    shutil.copyfile(signed / 'manifest.json', package / 'manifest.json')
    shutil.copyfile(signed / 'signature.txt', package / 'signature.txt')


def finalize(archive, signed):
    expected = d.metadata()
    if d.metadata(d.validate_zip(archive), expected['architecture']) != expected:
        raise ValueError('Unsigned build does not describe the selected release')
    d.OUTPUT.mkdir(parents=True, exist_ok=True)
    d.ARTWORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fennec-release.', dir='/private/tmp') as folder:
        stage = pathlib.Path(folder)
        d.run(['/usr/bin/ditto', '-x', '-k', archive, stage])
        app = stage / 'libvpn.app'
        d.run(['/usr/bin/xattr', '-cr', app])
        d.validate_app(app, expected)
        attach(app / 'Contents/Resources', signed)
        d.run(['/usr/bin/codesign', '--force', '--sign', '-', app])
        d.validate_app(app, expected)
        packed = stage / (expected['stem'] + '.zip')
        d.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', app, packed])
        destination = d.OUTPUT / packed.name
        shutil.copyfile(packed, destination.with_suffix('.zip.tmp'))
        destination.with_suffix('.zip.tmp').replace(destination)
    # A fresh keyless runner has no artwork/build cache from the build job.
    artwork = d.ARTWORK / 'artwork'
    d.run(['xcrun', 'swiftc', '-O', '-parse-as-library', '-swift-version', '5',
           d.ROOT / 'Tools/Artwork.swift', '-o', artwork, '-framework', 'Cocoa'])
    d.run([artwork, d.ARTWORK, expected['version'], expected['architecture']])
    subprocess.run([str(d.ROOT / 'Tools/package.sh'), str(destination)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'finalize'))
    parser.add_argument('input', type=pathlib.Path)
    parser.add_argument('signing', type=pathlib.Path)
    parser.add_argument('--source-root', type=pathlib.Path, default=pathlib.Path.cwd())
    args = parser.parse_args()
    # The workflow loads these tools from its reviewed commit, independently of
    # the immutable release tag. Older tags need only their existing unsigned build.
    d.ROOT = args.source_root.resolve()
    d.OUTPUT = d.ROOT / 'outputs'
    d.ARTWORK = d.ROOT / 'work/artwork'
    if args.action == 'prepare':
        prepare(args.input.resolve(), args.signing.resolve())
    else:
        finalize(args.input.resolve(), args.signing.resolve())


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Scan publication inputs, never ignored profiles, runtime state or build output."""
import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '8.30.1'


def source_snapshot(destination):
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT)
    for raw in sorted(set(names.split(b'\0')) - {b''}):
        relative = Path(os.fsdecode(raw))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe source path')
        source = ROOT / relative
        if not source.exists() and not source.is_symlink():
            continue  # A tracked deletion has no working-tree contents.
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as incoming:
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                raise ValueError('Source snapshot accepts regular files only')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=65536)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=('all', 'history', 'worktree', 'staged'), default='all')
    parser.add_argument('--gitleaks', default='gitleaks', help='Path to Gitleaks ' + VERSION)
    args = parser.parse_args()
    executable = shutil.which(args.gitleaks)
    if not executable:
        parser.exit(2, 'Install Gitleaks ' + VERSION + ' (macOS: brew install gitleaks).\n')
    actual = subprocess.check_output([executable, 'version'], text=True).strip().removeprefix('v')
    if actual != VERSION:
        parser.exit(2, 'Expected Gitleaks ' + VERSION + '; found ' + actual + '.\n')
    (ROOT / 'work').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='secret-scan.', dir=ROOT / 'work') as folder:
        stage = Path(folder)
        ignore = stage / 'empty-ignore';ignore.touch()
        flags = ['--config', str(ROOT / '.gitleaks.toml'), '--gitleaks-ignore-path', str(ignore),
                 '--ignore-gitleaks-allow', '--redact=100', '--no-banner', '--no-color']
        modes = ('history', 'worktree', 'staged') if args.mode == 'all' else (args.mode,)
        for mode in modes:
            if mode == 'worktree':
                snapshot = stage / 'source';snapshot.mkdir()
                source_snapshot(snapshot)
                command = ['dir', str(snapshot)]
            else:
                command = ['git', str(ROOT)] + (['--log-opts=--all'] if mode == 'history' else ['--pre-commit', '--staged'])
            print('Scanning ' + mode + ' with Gitleaks ' + VERSION, flush=True)
            subprocess.run([executable] + command + flags, cwd=ROOT, check=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
    except (OSError, ValueError):
        raise SystemExit('Secret scan failed while reading source inputs; no content was printed.')

"""Fixed entry points for the private, relocatable helper runtime."""
import pathlib
import ssl
import sys


def command(root, role, *arguments):
    if role not in ('bridge', 'engine', 'self-test', 'update'):
        raise ValueError('Unknown runtime role')
    root = pathlib.Path(root)
    return [str(root / 'Python/bin/python3'), '-I', '-S', '-B', str(root / 'launcher.py'), role] + list(arguments)


def tls_context():
    # Standalone OpenSSL does not know Apple's default CA bundle path. Use the
    # OS-maintained public roots explicitly, never a build-machine path/env var.
    return ssl.create_default_context(cafile='/etc/ssl/cert.pem' if sys.platform == 'darwin' else None)

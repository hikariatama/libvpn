"""Closed helper entry point; invoked only with an isolated, bundled Python."""
import json
import pathlib
import runpy
import sys


def main():
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise SystemExit('Fennec requires an isolated runtime')
    if len(sys.argv) < 2 or sys.argv[1] not in ('bridge', 'engine', 'self-test', 'update'):
        raise SystemExit('Unknown runtime role')
    root = pathlib.Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    role = sys.argv.pop(1)
    if role == 'update':
        if len(sys.argv) != 2: raise SystemExit('Invalid updater arguments')
        import updates
        updates.run(int(sys.argv[1])); return
    if role == 'self-test':
        # Import every native extension needed by the helper without connecting
        # to its socket or touching installed services, profiles or the network.
        import ctypes
        import fcntl
        import bridge
        import installation
        from runtime_support import tls_context
        context = tls_context()
        assert context.check_hostname and context.verify_mode == __import__('ssl').CERT_REQUIRED
        assert context.cert_store_stats()['x509_ca'] > 0
        print(json.dumps({'ok': True, 'pythonVersion': sys.version.split()[0], 'isolated': True}))
        return
    sys.argv[0] = str(root / (role + '.py'))
    runpy.run_module(role, run_name='__main__')


if __name__ == '__main__':
    main()

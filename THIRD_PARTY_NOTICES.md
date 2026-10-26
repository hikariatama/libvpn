# Third-party components

Fennec bundles sing-box 1.14.1, downloaded from the upstream release and verified
against an architecture-specific SHA-256 checksum in `Resources/fetch_runtime.py`.

- Project: https://github.com/SagerNet/sing-box
- Pinned source: https://github.com/SagerNet/sing-box/tree/v1.14.1
- Upstream license: https://github.com/SagerNet/sing-box/blob/v1.14.1/LICENSE

The release archive declares GNU GPL version 3 or later, with an additional
restriction on implying association or using the upstream application name.
The exact upstream notice is extracted from the verified archive and bundled as
`third-party/sing-box-LICENSE`; `third-party/GPL-3.0.txt` contains the GPL text.

The sing-box license and its redistribution requirements apply independently of
Fennec's licensing. No license has been selected for Fennec. The local build is
not a release publication workflow. Before distributing binaries, review the
upstream license, notices, corresponding-source obligations and build provenance.

User-provided rule sets are local user data. Fennec does not distribute them.

## Private helper interpreter

Fennec bundles CPython **3.14.8** from the Astral python-build-standalone release
**20261013**. Architecture-specific runtime and full build-metadata archives are
SHA-256 pinned in `Tools/fetch_python.py`.

- Project and source: https://github.com/astral-sh/python-build-standalone/tree/20261013
- CPython source: https://github.com/python/cpython/tree/v3.14.8
- Release: https://github.com/astral-sh/python-build-standalone/releases/tag/20261013

The app preserves the upstream `Python/PYTHON.json` build metadata and the entire
`Python/licenses/` directory, including CPython and statically linked dependency
notices (such as OpenSSL and libffi). `Python/lib/python3.14/LICENSE.txt` also
contains CPython's license. Only the interpreter and standard library needed by
the helper are shipped; pip, headers, tkinter and user packages are omitted.
The root helper receives the same runtime and notices during explicit installation.

## Build-only packaging tools

The DMG build uses [dmgbuild 1.6.7](https://github.com/dmgbuild/dmgbuild),
[ds_store 1.3.3](https://github.com/dmgbuild/ds_store) and
[mac_alias 2.2.3](https://github.com/dmgbuild/mac_alias). Universal wheels and
SHA-256 hashes are locked in `Tools/requirements-build.txt`. These tools run in
a local virtual environment and are not bundled with Fennec or its disk image.

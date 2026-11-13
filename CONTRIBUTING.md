# Contributing

Describe the problem and the expected behavior, then keep the change focused.
libvpn uses native macOS views and Python's standard library.
End users do not install Python or developer tools: a pinned standalone CPython
is bundled with the app and copied into the root-protected helper installation.

Run `./test.sh`, `./build.sh`, then `./test.sh` again. Full packaging needs
Python 3.10+ and uses hash-locked tools in an ignored virtual environment.
Use `./build.sh --app-only` for a shorter app/ZIP iteration. Real runtime validation uses
only the downloaded, checksum-verified build artifact. Inspect UI changes in
`--preview` and include sanitized screenshots when useful. Report actual checks
and their limits; mock tests are not evidence of live VPN behavior.

Never include real credentials, server endpoints, VLESS links, profiles or full
production diagnostics. Test configuration changes through observable outcomes,
transaction failures and forbidden side effects rather than source-string checks.
Live installation, updates, PF and VPN testing needs a prepared, explicitly authorized Mac.

Before committing, install Gitleaks 8.30.1 (`brew install gitleaks` on macOS), then
enable this repository's hook with `git config --local core.hooksPath .githooks`.
Run `python3 scripts/check-secrets.py` to scan all Git refs, the working sources,
and the staged diff. The working-tree scan copies only tracked and non-ignored
source files into a temporary directory; it never walks ignored build output,
local profiles or runtime state. All findings are fully redacted. The hook scans
the staged diff, and CI uses the same pinned version and project configuration.
Default detectors remain enabled; the VLESS detector additionally checks credential
URIs. Allow only specific synthetic identities in test paths, never entire test
directories, source trees or commits. Do not add real-secret baselines or inline
suppression comments to make a scan pass.
Private-runtime changes must preserve isolated `-I -S -B` launches, relocatability,
OS CA verification, dependency notices and validation before service replacement.

Developer ID signing, notarization, release publication and installers are separate
from the local build. A project license and contribution terms have not yet been
selected; agree on them with the owner before accepting third-party contributions.

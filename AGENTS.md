# Working on libvpn

## Build and check

- Native SwiftUI/AppKit sources are in `Source/`; `Resources/` owns the helper and runtime; `Tools/` owns build/packaging tools; `Tests/` uses synthetic data.
- Use the macOS 26 SDK and Python 3.10+ for full packaging. Run `./test.sh`, build with `env -u FENNEC_SIGNING_KEY_FILE CI=1 GITHUB_REF=refs/heads/main ./build.sh`, then run tests again against the bundled runtime.
- The CI build above must not read release signing keys. Signed packaging is a separate release operation; never generate or rotate keys implicitly.
- Keep pinned runtime hashes, isolated CPython (`-I -S -B`), relocatability and all third-party notices. End users need no developer tools or system Python.
- Verify deep/strict code signatures and the canonical ZIP/DMG with `Tools/distribution.py`. Packaging may mount only its own temporary images and must not install applications or helpers.
- Keep generated files in ignored `work/` and `outputs/`. Builds/tests must not request administrator access, change launchd/PF/routes, connect a VPN or use installed runtime state.

## Helper and data boundaries

- Preserve peer-UID checks, closed operations, bounded framing, strict field validation and atomic writes. Never accept arbitrary commands, paths or caller-selected trust keys.
- Mutations require the loaded revision. Keep failed drafts open and preserve the previous configuration until runtime readiness or completed rollback.
- Apply PF protection before opening TUN. Retain the transaction journal and stricter guard after failed recovery; status polling must not retry destructive operations.
- Protected DNS must not fall back to direct resolution. Keep route kill switches, fallback order, bypass semantics and transport exceptions intact.
- UUIDs, pins, private keys and authentication keys are write-only in editor responses. Blank replacements preserve existing secrets; clearing them is explicit.
- Profile exports contain unencrypted credentials: require explicit export, write atomically with mode 0600 and never include them in source or distributions. Bind import preview/apply to the same archive and revision.
- Preserve configuration/profile compatibility, bundle identifiers, helper labels, socket/state paths and installed release trust. Read-time migrations must preserve rollback behavior.
- Passwordless updates require the installed pinned public key, bounded archives, matching hash/architecture and increasing signed sequence. Never bypass verification or put the private signing key in artifacts.
- Validate and self-test staged runtime before changing services. Keep backup/rollback coverage for runtime, configuration, launchd and PF.
- Rule-set downloads and preparation are unprivileged; the helper validates bytes and stores immutable assets by digest. Preserve meaning when converting rule sets to editor conditions.

## Product constraints

- Ship no servers, destination presets or account-specific configuration. Each route owns its chain and kill switch.
- Editors use explicit Save/Cancel; errors preserve drafts. Confirm actions that remove protection or discard unsaved work. Keep progress inside the initiating control.
- Preserve native English/Russian UI and accessibility, user-provided names, compact route controls and scrollable lists.
- Preview and synthetic tests must never contact the live helper. Live installation, update and network behavior require separately prepared acceptance checks.

## Repository and releases

- Keep current development instructions in this file; do not add a documentation archive or a `docs/` directory.
- Keep README files concise and user-facing. Preserve licenses, third-party notices and asset attribution.
- Use synthetic fixtures. Never commit credentials, account data, private profiles, databases, local diagnostics, dependencies or build products.
- Keep one commit per version on `main`, named `vX.Y.Z`, with the matching version tag. Do not invent versions for repository maintenance.
- Published history, tags and assets may be replaced only with explicit owner authorization. Report source checks, builds, publication and deployed behavior separately.
- Keep deployment mirrors on the same reviewed source commit. A Git push does not authorize deployment or installation.

# Working on libvpn

## Structure and workflow

- Native macOS menu-bar app: use SwiftUI/AppKit, with no web runtime.
- `Source/` separates startup, models, state, popover, editors and icon drawing.
- `Resources/` separates configuration, protocol, runtime/PF generation,
  transactions, metrics, installation, updates and portable profile archives.
- `Tools/` owns build-only artwork, DMG layout and artifact validation.
- End users need no Python or developer tools. Keep the bundled interpreter
  isolated (`-I -S -B`), relocatable and inside the helper backup/rollback boundary.
- Preserve pinned runtime hashes and all dependency notices. Runtime self-tests
  may import modules but must never connect to the live helper or start services.
- Sources stay in root directories. Deliverables go to `outputs/`; intermediates,
  experiments and private archives go to `work/`. Both are ignored.
- Run `./test.sh` for changes. Run `./build.sh` for Swift/build/runtime changes,
  then run tests again to include the pinned build runtime checks.
- Full packaging requires Python 3.10+; `--app-only` uses only the native build prerequisites.
- DMG packaging may mount only its own temporary images. Never copy to Applications
  or launch an app/helper as part of packaging verification.
- Verify `codesign --verify --deep --strict outputs/libvpn.app`. File Provider
  metadata can interfere in an iCloud checkout: also verify the canonical ZIP
  after extraction outside iCloud. Validate versioned ZIP/DMG artifacts with
  `work/dmg-venv/bin/python3 Tools/distribution.py verify outputs/NAME.json --require-dmg`.
- Inspect UI changes with computer use in `--preview`. Preview must never call
  the live helper, register login launch, or install/change system components.
- Keep changes focused. Do not add dependencies for trivial changes.

## Product constraints

- Compact popover, no branding header or cards. Long group lists scroll.
- No default servers, countries, corporate rules, subscriptions or account integrations.
- Server and group editors use explicit Save/Cancel. Failed saves keep drafts open.
- VPN controls use small circular icons; loaders stay inside the clicked control.
- Each route owns its kill switch. Bypass pauses routing/protection without losing settings.
- Preserve the compact flag chains and per-route power, protection, reconnect and chain editor controls.
- Refresh belongs in the ellipsis menu. Use tooltips for extra context.
- Localize UI, accessibility labels and helper errors in Russian and English; inherit macOS language preferences. Preserve user-defined names.
- Passwordless helper updates require the installed pinned Ed25519 key, bounded archives and a newer signed sequence. Never bypass verification or put private signing keys in the repository or artifacts.

## System and publication boundaries

- Builds/tests must never request admin access, install daemons, alter PF/routes,
  switch a live VPN or depend on an installed executable/configuration.
- Installed applications and helpers are independent of this checkout. Do not
  overwrite them unless installation/testing is explicitly requested.
- Preserve closed helper operations, peer-UID checks, bounded framing, atomic
  configuration updates, durable rollback guards and protection-before-TUN ordering.
- Never read, copy, log or commit real UUIDs, keys, tokens, VPN profiles, private
  endpoints or account data while working on the repository. Use synthetic fixtures.
- Tests of installation/updates must patch privileged commands and use temporary
  synthetic directories. Report live checks separately from unit tests.
- Review working files and the existing Git index before publication. Do not commit
  binaries, scratch files, local diagnostics or personal paths. Repository preparation
  does not authorize committing, pushing, publishing or installing system components.
- Do not select a project license without the owner's instruction. Preserve mandatory
  third-party notices and review redistribution obligations before publishing binaries.

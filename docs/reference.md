# libvpn

A native macOS menu-bar client for configurable VLESS routing. libvpn runs one
sing-box TUN engine and lets you route ordered groups of destinations through
independent server chains.

## Install from a DMG

End users need macOS 14 or later and the matching Apple Silicon or Intel DMG.
Drag libvpn to Applications, open it, and explicitly authorize installation of
its system helper. Python, Xcode, Command Line Tools and an Apple Account are
not required on the user's Mac. The helper interpreter and sing-box are included
in the app; first launch does not download dependencies.

The build is ad-hoc signed and is not notarized. A downloaded app can be blocked
by Gatekeeper and require a manual launch exception in System Settings. This
distribution does not promise a warning-free first launch.

## Build from source

Builds require the macOS 26 SDK or later (Xcode 26+ or matching Command Line
Tools) and `/usr/bin/python3`. The app still supports macOS 14 and later;
Liquid Glass controls use native fallbacks on older systems.
The UI uses SwiftUI/AppKit; the helper uses a private bundled CPython runtime.
Full DMG builds additionally need Python 3.10+ on PATH (or `FENNEC_BUILD_PYTHON`).
Build-only Python packages are hash-locked and installed into `work/dmg-venv`.
Neither an Apple Account nor Apple Developer Program membership is needed by
the build scripts or CI. Developer ID signing/notarization is not configured.

```sh
./test.sh
./build.sh
./test.sh
codesign --verify --deep --strict outputs/libvpn.app
```

The build downloads sing-box **1.14.1** for the host architecture and verifies the
archive SHA-256. It also downloads architecture-specific **CPython 3.14.8** from
python-build-standalone release **20261013**, verifying both the stripped runtime
and full metadata/license archive. No pip, user packages, headers or Python tools
are shipped. The second test run includes real `sing-box check` cases using
that build artifact and relocates the private interpreter to a temporary folder.
After building, the Python test suite runs under both the build interpreter and
the shipped isolated interpreter to catch runtime compatibility differences.
On macOS, tests also parse synthetic PF rules with `pfctl -n`
without loading them. Builds and tests never install services, request administrator
access, modify PF/routes, or connect a VPN. The bundle is ad-hoc signed; this is
not a notarized release.

The default build generates a Retina app/volume icon and a read-only DMG.
The committed `Tools/Artwork/libvpn-dmg-banner.png` is a 1308×844 px source:
packaging keeps its original 2× pixels and adds a 1× representation in a TIFF
with a logical size of 654×422 pt. The Finder frame is 654×444 pt, including
the requested 22 pt title-bar budget; the artwork has no title-bar padding or crop.
The frame stays fixed on macOS 27, whose measured 32 pt title bar leaves 412 pt
visible and crops the bottom 10 pt. Icon coordinates are not shifted to compensate.
`Tools/dmg_settings.py` sets 140 pt icons, 12 pt labels below them and fixed
content coordinates: libvpn at (197, 195), Applications at (473, 195).
Finder layout metadata is written headlessly, with no sidebar, toolbar, tabs,
path bar or status bar. Local builds and CI use the same settings and verify
the mounted image's layout, DPI and original 2× pixels.
Finder can override the saved presentation with a user's global preferences
(for example, an always-visible tab bar); packaging does not change those preferences.
Finder controls the native label colors as well.
No packaging libraries are included in the application.

For version 1.0.0 on Apple Silicon, the distribution consists of:

- `outputs/libvpn-1.0.0-arm64.dmg`
- `outputs/libvpn-1.0.0-arm64.zip`
- `outputs/libvpn-1.0.0-arm64.sha256`
- `outputs/libvpn-1.0.0-arm64.json`

Names come from `Resources/Info.plist` and the build architecture (`arm64` or
`x86_64`). The JSON manifest records sizes, hashes, architecture, signing state
and the source commit when available. ZIPs preserve executable modes and bundle
signatures; the DMG is verified by mounting it read-only and checking its contents,
architecture, signature, background and icon positions. Validation also runs an
isolated interpreter self-test with hostile Python environment variables and
only system directories on PATH; no app/helper service is launched by that test.

```sh
libvpn_stem=$(/usr/bin/python3 Tools/distribution.py stem)
work/dmg-venv/bin/python3 Tools/distribution.py verify "outputs/$libvpn_stem.json" --require-dmg
(cd outputs && shasum -a 256 -c "$libvpn_stem.sha256")
```

Use `./build.sh --app-only` for app/ZIP builds without the DMG Python dependencies.
The working `outputs/libvpn.app` may receive Finder metadata in an iCloud checkout;
use the canonical versioned ZIP/DMG for distribution.

## GitHub Actions

Pushes to `main`, `v…` tags, pull requests and manual runs build both Apple Silicon (`macos-15`) and
Intel (`macos-15-intel`) independently. Each job tests before/after building,
validates the complete distribution and uploads its four versioned files as a
14-day artifact. Download them from the run's **Artifacts** section.

The workflow pins action commits, uses read-only repository permissions and does
not persist checkout credentials. It caches only the checksum-verified runtime
archives and hash-locked pip downloads. Superseded runs are cancelled. A `v…` tag
must match the bundle version. No GitHub Release is created or published.

CI does not need signing secrets. Developer ID signing/notarization and live
helper/VPN tests remain separate work. First-launch helper installation is always
an explicit action; dragging the app from the DMG does not install services.

## First launch

1. Open the built app. Installing its system helper is a separate, explicit
   administrator-authorized action in the popover.
2. Open **Настройки… → Серверы**. Add a VLESS/TCP/TLS server manually or paste a
   compatible `vless://` link. No servers or routing lists are shipped.
3. Under **Правила**, add groups and their ordered chains. Configure **Остальной
   трафик** for destinations that match no group.
4. Save each editor to apply it. Cancel discards the draft; a failed save keeps it
   open with an error. Removing a referenced server requires removing its chain
   references first.

A helper update starts automatically when the installed helper differs from the
app bundle. Installed helpers verify Ed25519-signed packages and update without
an administrator prompt. First installation, upgrading an older helper without
this mechanism, and manual repair still require administrator authorization. A cancelled or failed update
is not retried by polling or relaunching the same app build; use
**… → Восстановить помощник…** to retry. The installer preserves configuration,
validates the staged private runtime before stopping services, and restores the
previous installation on failure.
The installed helper owns its own root-protected runtime and does not depend on
the app remaining at its original path or the DMG remaining mounted.

A fresh installation has no servers, no groups, direct fallback traffic and the
kill switch disabled. The interface follows macOS language preferences, with Russian and English localizations. Login launch
is controlled in the ellipsis menu.

## Routing and TLS

Groups match exact domains, domain suffixes (including subdomains), and IPv4/IPv6
CIDRs. Conditions within a group are alternatives; the first matching group wins.
Server transports and local networks have system exceptions before user groups.
A chain cannot contain the same server twice. Local rule-set snapshots
remain attached to their groups until explicitly removed.

Supported links use `security=tls`, TCP, and `encryption=none`. Optional parameters
are `sni` or `serverName`, and `pinSHA256`. Unsupported or ambiguous parameters,
including REALITY, WebSocket, uTLS fingerprints and insecure TLS, are rejected.
Certificates use system CAs and server-name verification by default. A supplied
SHA-256 public-key pin uses sing-box's certificate-pinning mode. Existing UUIDs
and pins are write-only: blank fields preserve them; removing a pin is explicit.

## Route protection and bypass

Each route, including fallback, has its own kill switch. Disabling a protected
route blocks its matching traffic; disabling an unprotected route uses direct
routing. Power, protection, reconnect and a compact chain editor remain available
in each popover row. Chains use country flags; server names appear in tooltips
and editors. Server and chain lists scroll without a five-item limit.

Bypass is available in the ellipsis menu. It temporarily sends traffic directly and suspends protection. Route chains,
power states and protection choices are retained and restored when bypass ends.

The supervisor keeps TUN running for disabled protected routes. While TUN is
healthy, unprotected routes can use direct egress. If the engine stops or a
transaction/rollback is incomplete, PF closes external IPv4/IPv6 and direct DNS
until the engine confirms a working TUN. This emergency guard is broader than
individual domain rules: PF cannot classify arbitrary domain traffic. Local
networks and configured VPN transports remain exceptions. Disabled protected domain rules reject DNS requests as well. Other DNS requests
use an enabled chain when available and HTTPS DNS for direct routes otherwise.
IP-only conditions apply to destination connections; DNS names cannot be
classified by an IP condition before resolution.

Applying protection clears existing PF states. This can reset other applications'
connections. libvpn never disables PF globally. Its guard applies across physical
interface changes and does not persist an ephemeral TUN exception at boot.

Domain endpoints are resolved before use and cached. While protected, refreshes
use authenticated loopback SOCKS plus HTTPS DNS through an enabled chain. A
stopped tunnel cannot bootstrap a new hostname through direct DNS; configure an
IP endpoint or restore the tunnel. Protected DNS does not silently fall back.

Reachability samples show ICMP availability of added servers, not proof of a
working VPN chain. The graph counts traffic across the Mac's physical interfaces.

## Profiles and updates

**Настройки → … → Импортировать…** opens a `.libvpn` archive (legacy `.fennec` archives are also accepted) and shows the
server/group/rule-set counts and the resulting kill-switch state before replacing
anything. Cancel leaves the current configuration unchanged. A failed apply keeps
the preview open and restores the previous configuration through the normal transaction.
Import replaces the complete configuration; it does not merge it.

**Экспортировать настройки…** creates a portable archive containing servers,
credentials, ordered groups, fallback routing, protection settings and attached
local binary rule sets. Cached DNS addresses and local revision numbers are not
portable state. The file is **not encrypted** and includes UUIDs and TLS pins.
The app warns before export and writes it atomically with mode `0600`. Keep it
private; do not attach it to public issues or commit it to a repository.

To update libvpn, replace the app in Applications with the newer DMG's copy.
Root-owned settings remain outside the app. The UI detects a different helper
build and starts a signed update automatically, without asking for a password.
After failure, the recovery command remains available in the menu. The update
validates the replacement before stopping services and preserves
settings and rule-set files. Failed updates restore the previous helper, runtime,
launchd files and PF configuration from a closed backup. No remote updater or
background download is required. Downgrades with an unsupported schema are rejected.

See [signed helper updates](updates.md) for release keys, CI signing and trust boundaries.

## Repository

- `Source/`: native app entry point, models, state and views.
- `Resources/`: configuration, bounded helper protocol, runtime/PF generation,
  transactions, installation and updates and interface counters.
- `Tests/`: synthetic Python tests and executable Swift model checks.
- `Tools/`: artwork generation, headless DMG packaging and distribution validation.
- `docs/`: configuration and system integration details.
- `work/`, `outputs/`: ignored scratch space and local deliverables.

Installation, updates and live packet-filter behavior require a separately
prepared test Mac. Synthetic checks and runtime validation do not establish that
those privileged paths work on every network configuration.

Licensed under AGPL-3.0-only. See [third-party notices](../THIRD_PARTY_NOTICES.md)
for the bundled runtime. No commit, push, release upload or installation is part
of the build workflow.

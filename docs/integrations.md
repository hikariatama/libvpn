# System integration

The app starts `Python/bin/python3 -I -S -B launcher.py bridge` from its Resources
directory, without action payloads in process arguments. No system Python or
developer-tool shim is invoked. Isolation ignores Python environment variables,
user site packages and the current working directory; bytecode writes are disabled.
One newline-delimited JSON request travels over stdin. The bridge connects to
`/var/run/local.fennec.sock` and returns one JSON response. Requests are limited to
2 MiB; duplicated JSON fields, unknown fields/actions and incomplete frames are
rejected. Socket ownership and peer UID limit access to root and the configured user.

Read operations are `status`, `configuration`, `metrics` and `traffic`. Mutations
are `save-node`, `delete-node`, `save-group`, `delete-group`, `reorder-groups`,
`set-enabled`, `set-kill-switch`, `reconnect` and `refresh-endpoints`. Editor
mutations carry a revision. Operations return `{ok, configuration? , error?}`;
status additionally contains installation version and engine state. A successful
response follows runtime readiness, not merely request acceptance.

Two launchd services use the root-owned copy of the same interpreter and launcher
to run `engine.py` and `bridge.py daemon UID`. The entire private runtime is copied
and self-tested in staging before any services are changed. It is included in
the existing secure backup/rollback boundary. The engine applies
PF before opening TUN, checks the generated sing-box runtime and publishes a
sanitized heartbeat. It discovers its own newly created `utun` interface; physical
interfaces are not enumerated in the guard. The persistent anchor never permits
an ephemeral TUN name across boot. The helper serializes mutations and recovers
an unfinished transaction journal before reporting status or accepting another
mutation. Recovery runs once per journal; a failed attempt retains the journal
and stricter guard, reports a recovery error, and is not retried by status polling.
An explicit reconnect can retry recovery. The repair installer also restores the
pre-transaction configuration and keeps the journal guard until readiness.

The optional protected hostname refresh uses an authenticated mixed proxy on
127.0.0.1:18100. HTTPS DNS connects to a numeric resolver address through an enabled
chain. Endpoint cache refresh runs every five minutes while the engine is active;
unchanged addresses do not restart it. No direct DNS fallback is used while protected.
The private OpenSSL runtime explicitly loads Apple's `/etc/ssl/cert.pem` public
root bundle for HTTPS DNS, with certificate and hostname verification enabled.
VLESS TLS continues to use sing-box's trust/pinning configuration.

First installation is an explicit UI action. A helper build mismatch automatically
starts a signed update through the existing helper, without administrator authorization.
Older helpers need one administrator-authorized installation of the verifier and
pinned key. See [signed updates](updates.md). The app records the attempted target
build before starting the update, preventing repeated attempts after cancellation,
failure or relaunch. Manual repair remains in the menu. Transient status errors
do not count as build mismatches. State is stored outside the app, so
replacing the application bundle preserves settings.

Profile operations are `export-profile`, `preview-profile` and `import-profile`.
Only the explicit export operation returns credentials, inside a base64 ZIP archive.
Imports use the same archive for preview and apply, bind the confirmation to its
SHA-256 and current revision, and use the normal runtime/PF transaction. Archive
requests/replies are bounded at 48 MiB (32 MiB binary archives); ordinary requests
remain limited to 2 MiB. Uncompressed content is capped at 64 MiB. Rule sets are
verified by SHA-256 and stored under generated digest filenames, never extracted
using archive paths. A new installation starts with empty direct routing and
protection disabled, then can import a profile from Settings.

The local build does not touch `/Applications`, launchd, PF or active VPN state.

The optional `language` request field accepts only `en` or `ru`. Fixed error messages
are localized at the protocol boundary; user-defined names and profile data are
never translated. AppKit and SwiftUI use the bundled `.lproj` string resources,
with English as the development language. macOS selects the preferred supported
language, including its standard per-app language setting.

The product and distribution name is `libvpn`. The existing bundle identifier,
helper labels, UNIX socket, on-disk state directory, profile payload identifiers
and signing-key location are stable compatibility identifiers. A display-name
change must not reset settings, install parallel services or rotate release trust.

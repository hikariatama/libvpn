# Security

libvpn has a root helper, a sing-box TUN engine and a PF guard.
Changes to those components need review as security-sensitive code.

- The UNIX socket is `0600`, owned by the configured user. The helper checks peer
  UID and accepts a closed vocabulary of bounded JSON requests; no shell commands
  or caller-selected filesystem paths are accepted.
  Ordinary requests are capped at 2 MiB before JSON decoding. Larger requests
  require a bounded action/length envelope and explicit admission before their
  body is sent. Only one large transfer may be in flight, with a shared encoded
  byte budget held through dispatch. New clients negotiate this capability and
  retain the legacy wire format when updating an older installed helper. Older
  clients cannot send large legacy frames to a new helper; they must be updated.
- Full configuration, generated runtime, DNS-proxy credentials, rule snapshots and
  backups are root-owned. Configuration and secret files use mode `0600`.
- Normal status omits endpoint metadata, rule matchers and credentials. An explicit editor request exposes
  editable endpoint metadata, but not existing UUIDs or pins. Secret updates travel
  over stdin/socket, never command arguments. Raw engine output is not logged.
- Mutations check the revision and complete runtime/PF candidate before activation.
  A durable transaction journal retains the old configuration and a protective guard.
  Failed rollback retains that guard for recovery when the helper restarts.
- Global protection covers external IPv4/IPv6 and DNS. Exceptions include local
  networks and configured server transport endpoints. These exceptions are part of
  the trust boundary; the app cannot prevent other processes from reaching them.
  Per-route protection relies on sing-box routing decisions. With an active TUN,
  the PF rules retain unrestricted physical egress to preserve existing direct
  traffic, including ICMP. A local process that deliberately avoids TUN routing
  can bypass per-route protection; this finding remains open. Restricting physical
  egress requires a separate trusted engine identity and an explicit decision
  about direct protocols that PF cannot associate with a socket owner.
- An enabled tailnet adds two protection exceptions: UDP from its fixed WireGuard
  port, and TCP to the resolved coordination server when it is reached directly.
  Inbound tailnet connections reach only the listed loopback ports; sing-box delivers
  connections to the node's own address as `127.0.0.1`, so the final reject rule is
  what keeps other local services closed. The node identity lives in a root-owned
  state directory that is never exported, and the auth key is write-only. The engine
  sets `TS_NO_LOGS_NO_SUPPORT=true`, so the embedded client does not upload logs to
  Tailscale.
- Domain-endpoint refresh while protected uses authenticated local proxy access and
  HTTPS DNS through a configured chain. It does not fall back to system DNS.
- Applying protected startup flushes PF states to close pre-existing direct flows.
  This is a system-wide side effect performed only by installed privileged code,
  never by builds or automated tests.
- Profile export is an explicit secret-bearing operation. Normal status and editor
  replies never contain an archive. Exports are unencrypted and saved as `0600`;
  imported archives are bounded, contain only a configuration and content-addressed
  rule sets, and are never extracted using caller-provided filesystem paths.
  Only stored/DEFLATE ZIP entries and reviewed ZIP flags are accepted, before
  opening any member decoder. Native import rejects links and special files,
  caps regular-file reads and performs them off the main thread.
- Import validates the full configuration and immutable rule sets before its
  revision-checked transaction. Preview and cancellation do not change settings.
- Both sing-box and CPython runtime archives are version/checksum pinned. The
  private interpreter ignores Python environment variables and user site packages,
  and never imports from the current working directory. Installed copies are
  root-owned and restored with the rest of the helper on failed replacement.
  HTTPS DNS loads the OS public CA bundle explicitly, rather than build-time paths.
  No destination lists are downloaded by the build or by a newly installed app.
  Rule sets are downloaded only when a user adds or updates them, by the unprivileged
  bridge over HTTPS with size limits. The root helper accepts only their bytes,
  validates them with sing-box and stores them under their SHA-256.
- The first-install payload hash is compiled into the native executable. System
  tools verify the copied archive in a private privileged stage before any
  candidate code runs; later updates still require the installed Ed25519 anchor.
  Ad-hoc signing does not authenticate the initial native app's publisher.
- PF anchor and main configuration replacements use validated temporary files,
  file and parent-directory fsync, and atomic rename. Startup and child-loss
  guards derive state flushing from the effective transaction journal guard.

There is deliberately no new aggregate configuration-size or matcher-count cap.
Large configurations can still exhaust resources despite individual request bounds.

Tests use synthetic fixtures. Root service installation, recovery on real macOS
networks and packet-level leak protection still require isolated live validation.
Do not treat `sing-box check`, code signing or UI previews as a live security audit.

Report vulnerabilities privately to the repository owner. Do not post production
credentials, profiles, links or private endpoints in issues.

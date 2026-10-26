# Security

libvpn has a root helper, a sing-box TUN engine and a global PF kill switch.
Changes to those components need review as security-sensitive code.

- The UNIX socket is `0600`, owned by the configured user. The helper checks peer
  UID and accepts a closed vocabulary of bounded JSON requests; no shell commands
  or caller-selected filesystem paths are accepted.
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
- Domain-endpoint refresh while protected uses authenticated local proxy access and
  HTTPS DNS through a configured chain. It does not fall back to system DNS.
- Applying protected startup flushes PF states to close pre-existing direct flows.
  This is a system-wide side effect performed only by installed privileged code,
  never by builds or automated tests.
- Profile export is an explicit secret-bearing operation. Normal status and editor
  replies never contain an archive. Exports are unencrypted and saved as `0600`;
  imported archives are bounded, contain only a configuration and content-addressed
  rule sets, and are never extracted using caller-provided filesystem paths.
- Import validates the full configuration and immutable rule sets before its
  revision-checked transaction. Preview and cancellation do not change settings.
- Both sing-box and CPython runtime archives are version/checksum pinned. The
  private interpreter ignores Python environment variables and user site packages,
  and never imports from the current working directory. Installed copies are
  root-owned and restored with the rest of the helper on failed replacement.
  HTTPS DNS loads the OS public CA bundle explicitly, rather than build-time paths.
  No destination lists are downloaded
  by the build or by a newly installed app.

Tests use synthetic fixtures. Root service installation, recovery on real macOS
networks and packet-level leak protection still require isolated live validation.
Do not treat `sing-box check`, code signing or UI previews as a live security audit.

Report vulnerabilities privately to the repository owner. Do not post production
credentials, profiles, links or private endpoints in issues.

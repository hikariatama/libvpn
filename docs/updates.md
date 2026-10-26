# Signed helper updates

The app keeps its native ad-hoc macOS code signature. Helper packages use a separate
Ed25519 release signature, verified with the system CryptoKit framework. This does
not provide Developer ID signing, notarization or a Gatekeeper exception.

The first helper installation (and manual repair) requires administrator approval.
It installs the public release key and verifier in the root-owned helper directory.
Later app versions send their bundled helper archive through the authenticated
UNIX socket. The installed helper verifies the signature, archive hash, architecture
and increasing release sequence before extracting into a private root-owned stage.
It rejects paths, symlinks, duplicates, oversized archives and mismatched receipts.
The caller cannot provide commands, destination paths or a replacement trust key.

The update runs as a separate temporary launchd job, so replacing the helper cannot
kill its installer. Installation uses the existing backup, rollback and protection
boundary. The temporary launchd plist is removed after completion, so the installer
does not rerun at boot. The UI waits for the installed build to report readiness.
Failed updates retain manual repair; polling does not repeatedly request a password.

## Release signing

`Resources/update-public-key.txt` is public and belongs in version control. The
private key must never be added to this repository, app bundles, DMGs or CI artifacts.
Keep a protected backup: losing it prevents automatic updates for existing users.
Changing the pinned key currently requires administrator-authorized repair.

Local builds use `$HOME/.config/fennec/signing/release.key`, or the file specified by
`FENNEC_SIGNING_KEY_FILE`. The key is a raw 32-byte Ed25519 seed with mode `0600`.
The native tool in `Tools/PackageSignature.swift` creates a key pair with its
`generate PRIVATE_KEY_FILE PUBLIC_KEY_FILE` command and refuses existing files.
Builds never generate or rotate release keys implicitly. Forks must deliberately
create their own key pair before publishing signed updates.

For CI, configure the repository secret `FENNEC_UPDATE_PRIVATE_KEY` with the base64
encoding of the private seed. Do this through GitHub's secret settings; do not paste
it into an issue, workflow source or build log. CI writes it to a temporary private
file, signs and verifies against the committed public key, then removes the file.
It is never exposed to pull-request jobs. Tag builds fail if the signing secret is
missing. PR builds may produce unsigned preview artifacts, which cannot update a
helper through the passwordless path. CI does not publish or install releases.

Every signed app contains `Contents/Resources/HelperUpdate/` with `helper.zip`,
`manifest.json` and `signature.txt`. The manifest binds the archive's SHA-256 and
size to its helper build, architecture and increasing sequence. Signatures cover
the exact manifest bytes, not a reserialized copy.

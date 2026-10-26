# Configuration v9

The root helper owns `Engine/configuration.json` under
`/Library/Application Support/Fennec`. Users edit it through the native UI rather
than changing the file while services are running.

The top-level fields are `schemaVersion` (9), a monotonic `revision`, `nodes`,
ordered `groups`, `fallback` and `bypass`. Each route owns a boolean `killSwitch`.
The legacy top-level `killSwitch` is accepted for existing v9 profiles and expands
to all route switches during normalization; normalized output sets it to false. An empty registry
and empty groups are valid. Enabled routes require a nonempty, nonrepeating chain
of known server IDs. Node IDs and group IDs are stable opaque identifiers.

Nodes contain display name/flag/country, endpoint address/port, VLESS UUID and TLS
settings. Only VLESS/TCP/TLS is supported. Cached DNS addresses and private
inter-node transport metadata are internal fields and cannot be supplied through
normal editor requests. Existing UUIDs and pins are never returned by the editor
API. Blank replacements retain them; removing a pin requires `clearPin: true`.
Optional TLS minimum/maximum versions accept `1.2` and `1.3`; a reversed range
is rejected. These constraints survive editing, exports, imports and updates.

Groups contain `id`, `title`, `flag`, `enabled`, `killSwitch`, `nodes`, `domains`, `suffixes`,
`cidrs` and `snapshots`. Each populated matcher becomes an independent alternative.
Groups are evaluated in stored order. Fallback has no matchers and always comes
last. A snapshot ID is the SHA-256 of an immutable imported binary `.srs` asset.
Normal edits can retain or remove existing references, but cannot introduce an
arbitrary filesystem path or attach another group's private snapshot.

A mutation supplies the revision that was loaded for its draft. Stale revisions
are rejected; the draft remains open so it can be copied/reviewed before reopening
with fresh data. Validation errors and runtime failures leave the old revision
active. A transaction journal stores the previous version and a guard covering
both old/new transport endpoints until success or completed rollback.

`set-kill-switch` requires a route ID. `set-chain` replaces only that route's ordered
node IDs; `set-bypass` retains all route data. Each request requires the current
revision. No arbitrary paths or command arguments are accepted.

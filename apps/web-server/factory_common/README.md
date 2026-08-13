# factory_common (vendored from the Factory hub)

This package is a **byte-identical vendored copy** of the canonical
`shared/factory-common/factory_common/` layer in the
[Factory hub](https://github.com/olafkfreund/Factory) - the single source of
truth for the fleet's deduped, stdlib-only utility primitives (epic Factory#154,
issue Factory#161):

- `factory_common.logsafe` - `sanitize_log()`, the CWE-117 / `py/log-injection`
  fix: escapes CR/LF and control characters in a value before it reaches a log
  message, so untrusted input cannot forge a log record.
- `factory_common.secrets` - the canonical secret-pattern table + `redact()` /
  `scan()` / `contains_secret()`.
- `factory_common.http` - the Cloudflare-friendly typed `urllib` JSON client.

It sits beside `server/` rather than inside it because the modules use absolute
imports (`from factory_common.http import ...`), so the package must be
top-level. `apps/web-server` is already the import root - `server.main:app` is
how the app is launched - so `from factory_common.logsafe import sanitize_log`
resolves without any path juggling.

## Why vendored (not pip-installed)

The fleet vendors shared layers byte-for-byte behind a drift gate rather than
publishing a package, exactly as AIFactory already does at
`apps/backend/factory_common/`. This keeps CI and the coder pod dependency-free
(the layer is stdlib-only and importable anywhere) while a gate guarantees the
copy cannot silently drift from the hub.

## Do not edit here

These files are owned by the hub. To change the behaviour, land the change in
`shared/factory-common/` in the Factory hub first, then re-vendor here and bump
`.hub-sha` to the new hub commit.

## Pinned hub commit

See `.hub-sha`.

## Consumers in this repo

`sanitize_log` is applied to untrusted values interpolated into log messages
across `apps/web-server/server/` (routes, services, websockets) - see
`apps/web-server/tests/test_logsafe_vendored.py` for the behaviour lock.

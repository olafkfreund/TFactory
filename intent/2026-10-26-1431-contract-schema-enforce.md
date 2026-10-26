---
status: draft
issue: 1431
author: olafkfreund
predecessor: intent/2026-10-24-1431-contract-sender-schema.md
---

# Intent: enforce the task-contract schema at ingest by default (#1431)

## Problem

`POST /api/specs` checks an incoming RFC-0002 task contract against the vendored
schema (#1427), but `TFACTORY_CONTRACT_SCHEMA_MODE` defaults to `warn`, so an
invalid contract is stored and planned from anyway. Warn was the default only
until the sender was fixed. #1438 (released in 0.9.42) fixed it with a
schema-valid proof-run ingest template.

## Proposed outcome

With the variable unset, an ingest whose contract fails the schema gets a `400`
and no spec dir is created. `TFACTORY_CONTRACT_SCHEMA_MODE=warn` remains as the
opt-out. A request without a contract is unchanged.

## Affected users and systems

TFactory backend (spec ingest), the factory cluster deployment, anyone posting
hand-made contracts.

## Constraints

- Ship only after 0.9.42 (the valid template) is running. Done.
- factory-gitops must not set the old value explicitly. Checked: no manifest
  under `apps/*/manifests` sets `TFACTORY_CONTRACT_SCHEMA_MODE`.
- Error text keeps naming field paths and validators only, never values (#718).

## Open questions

None. The user pre-approved flipping the default for the 2026-10-26 wave.

---
status: approved
issue: 1431
author: olafkfreund
predecessor: intent/2026-10-24-1427-contract-schema-gate.md
---

# Intent: make the contract sender emit a schema-valid contract (#1431)

## Problem

The #1427 live check found 7 of 14 stored contracts fail the vendored schema
(`apps/backend/contracts/task-contract-v2.schema.json`). #1431 asks to fix the
sender, then flip `TFACTORY_CONTRACT_SCHEMA_MODE` to `enforce`.

Finding the sender (2026-10-24, read-only):

- AIFactory, PFactory and TFactory `origin/dev` contain no code that writes
  `contract_version: "1.0"`. PFactory's `build_task_contract` already emits a
  schema-valid v2 contract and validates it. AIFactory's ingest payload
  (`pfactory/tfactory_client.py::build_ingest_payload`) forwards that contract,
  or a marker-less thin one that TFactory neither validates nor stores.
- The 5 `"1.0"` contracts are the `postdeploy-10xx-*-nix` release proof runs.
  Their ingest bodies were written by hand by Factory operator sessions and
  copied from one run to the next. The contract in them is
  `{"contract_version": "1.0", "environment": {...nix...}}`.
- The 2 contracts with no `phases` (`026-`/`027-myfriends-*`, around
  2026-09-30) have the same hand-posted verify-spec shape. The body that
  produced them was not recovered.

So the "sender" is a template that lives nowhere and is hand-copied. Nothing
checks it against the schema.

## Proposed outcome

- TFactory carries one checked-in proof-run ingest body whose `contract` is
  schema-valid (`contract_version: "2"`, `feature`, `workflow_type`, `phases`,
  plus the nix `environment` block the proof runs need).
- A test validates that body's `contract` against the same vendored schema
  through `validate_task_contract`, so the template cannot drift.
- The ingest docs point at it, so the next proof run copies a valid body.

## Affected users and systems

- TFactory: a new example file under `scripts/`, one test, one docs line.
- Operator sessions that run post-deploy proof runs.
- No change to AIFactory or PFactory, and no runtime code change.

## Constraints

- Do not flip the `TFACTORY_CONTRACT_SCHEMA_MODE` default in this lane.
- Reuse the vendored schema and `validate_task_contract`. Do not add a second copy.
- No secrets, project ids or hostnames in the template.

## Open questions

- Where does the fix live? Decided (lead recommendation, user-approved
  2026-10-24): fix the sender so that it always emits a schema-valid contract,
  and test it against the vendored schema. The sender turned out to be the
  hand-written proof-run body, not code in AIFactory or PFactory. So the fix is
  a checked-in, schema-tested template in TFactory, which owns the schema and
  the proof runs.
- `contract_version` type. The brief said "integer". The schema enum is the
  strings `"1"` and `"2"`, and PFactory emits `"2"`. Follow the schema and use
  `"2"`.
- Flip to `enforce`. This is not blocked on a release of a code sender. It
  needs only the next proof runs to use the template. Left to the lead as a
  follow-up.

---
status: draft
issue: 1427
author: olafkfreund
---

# Intent: Validate the incoming task contract against its schema at ingest

## Problem

`POST /api/specs` accepts a `contract` and `create_spec_ingest_workspace`
writes it to `context/task_contract.json`
(`apps/backend/agents/tools_pkg/tools/task_control.py:508-515`) after checking
only that it has a `tfactory` or `contract_version` key. `read_task_contract`
(`apps/backend/agents/task_contract.py:77`) and `parse_tfactory_profile` are
tolerant, so a malformed contract becomes the authoritative test profile
(lanes, frameworks, `ac_to_code_map`) the verify gate uses. The schema copy
TFactory vendors (`apps/backend/contracts/task-contract-v2.schema.json`) is
only checked for drift in CI (`tests/test_schema_drift.py:210`), never used at
runtime.

## Proposed outcome

- A contract that fails the vendored schema is rejected at ingest with HTTP
  400 and an error naming the failing fields, before any workspace is created
  (enforce mode).
- Because live contracts do not all pass today (see below), the gate ships in
  warn mode by default: the contract is still accepted, the failing fields are
  logged and returned in the ingest response's `warnings`. An operator flips
  `TFACTORY_CONTRACT_SCHEMA_MODE=enforce` once senders are fixed.
- A request with no contract behaves exactly as today.

## Affected users and systems

- TFactory backend (`task_control.py`, `task_contract.py`) and the
  `POST /api/specs` route (`apps/web-server/server/routes/specs.py`), which
  already maps `ValueError` to 400.
- Senders of contracts: AIFactory handoff and any service-token holder.

## Constraints

- Fail closed in enforce mode: an invalid contract is a 400, not a warning.
- Reuse the vendored schema; no second copy.
- No contract content in logs or errors: field paths and validator names only.
- Signature verification is out of scope (follow-up issue).

## Live check (2026-10-24, read-only)

The vendored schema was run, piped over stdin (nothing written in the pod),
against every `context/task_contract.json` under `/home/nonroot/.tfactory` in
the live tfactory pod: 14 contracts, 7 pass, 7 fail.

| Project id | Count | Failing fields |
| --- | --- | --- |
| `62818c7a-abfb-4ac0-8159-8186d2e7af5e` | 5 (newest 2026-10-23) | `contract_version` (`"1.0"` not in enum `1`,`2`); required `feature`, `workflow_type`, `phases` missing |
| `bffa70f5-f437-4d45-8865-e5272e1daa9e` | 2 (2026-10-01) | required `phases` missing |

So a sender is still posting thin `contract_version: "1.0"` contracts.
Enforcing today would break those handoffs.

## Open questions

- Gate mode default. Decided (lead recommendation, user-approved
  2026-10-24): add `TFACTORY_CONTRACT_SCHEMA_MODE=warn|enforce`, default
  `warn`, because the live check found 7 failing contracts. Follow-up issue to
  fix the sender, then flip the default to `enforce`.
- Signature verification. Decided (lead recommendation, user-approved
  2026-10-24): out of scope here; follow-up "verify the contract approval
  signature with the signer's public key (TFactory verifies; AIFactory does
  not re-sign)".

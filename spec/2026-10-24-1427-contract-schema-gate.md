---
status: approved
issue: 1427
intent: intent/2026-10-24-1427-contract-schema-gate.md
---

# Spec: Validate the incoming task contract against its schema at ingest

## Design

- **D1 Validator.** Add `validate_task_contract(contract: dict) -> list[str]`
  to `apps/backend/agents/task_contract.py`. It builds one cached
  `jsonschema.Draft202012Validator` from the vendored
  `apps/backend/contracts/task-contract-v2.schema.json` (path resolved from
  `__file__`, the way `agents/completion_schema.py:34-38` does) and returns
  the sorted, de-duplicated list of `"<json path>: <validator>"` strings
  (path joined with `/`, `<root>` for the top level, e.g.
  `"<root>: required"`, `"contract_version: enum"`). Never the error
  `message`: it quotes the contract's values. Empty list means valid.
  `format` is not asserted (Draft 2020-12 default), so `date-time` strings are
  not checked; that matches the live check.
- **D2 Gate.** In `create_spec_ingest_workspace`
  (`apps/backend/agents/tools_pkg/tools/task_control.py`), after the `ref`
  checks and before `spec_dir.mkdir` (so a rejection leaves no workspace),
  validate the same contracts that get persisted: a `dict` with a `tfactory`
  or `contract_version` key. Mode is read per call from
  `TFACTORY_CONTRACT_SCHEMA_MODE` (strip, lower), default `warn`:
  - errors and mode `warn`: append
    `"task contract fails schema (warn mode): <errors joined by '; '>"` to
    `warnings`, log it at WARNING, and carry on as today.
  - errors and any other value (`enforce`, or a typo): raise
    `ValueError("task contract fails schema: <errors>")`. Fail closed: an
    unknown mode enforces.
  - no errors, no contract, or a contract without the markers: unchanged.
- **D3 HTTP.** No route change for the status: `routes/specs.py:334-349`
  already turns `ValueError` into 400 with the message shown to the caller
  (`InputRejectedError`). Update that comment's list of verified raise sites
  to include the schema gate (its text is field paths and validator names
  from the caller's own contract). The MCP tool caller
  (`task_control.py:1013-1024`) already returns `ValueError` as an error.
- **D4 Dependency.** `jsonschema` is only transitive today (4.26.0 in the
  image and in the dev venv). Add `jsonschema>=4.18` to
  `apps/backend/requirements.txt` so the runtime import is declared.
- **D5 Default.** `warn`, because the read-only live check on 2026-10-24
  found 7 of 14 stored contracts failing (intent, Live check). No chart
  change: the default needs no env var.
- **Follow-ups (filed, not built):** (a) find and fix the sender of
  `contract_version: "1.0"` contracts without `feature`/`workflow_type`/
  `phases`, then flip the default to `enforce`; (b) "verify the contract
  approval signature with the signer's public key (TFactory verifies;
  AIFactory does not re-sign)".

## Alternatives rejected

- **Enforce by default now.** Breaks the live sender that posts
  `contract_version: "1.0"` contracts (5 in the last week).
- **Validate in `read_task_contract`.** Read time is too late: the workspace
  exists and a bad contract would only be dropped silently, not rejected.
- **Pydantic model for the contract.** A second copy of the schema that would
  drift; the vendored JSON schema is already drift-checked in CI.
- **Unknown mode falls back to warn.** A typo such as `enforced` would
  silently disable the gate; fail closed instead.
- **Signature verification here.** Needs key distribution; separate issue.

## Risks

- Warn mode changes nothing for callers except an extra `warnings` entry; the
  existing tests that send thin `{"contract_version": "2", "tfactory": ...}`
  contracts keep passing because the default is warn.
- Enforce mode on the live pod would 400 the current `"1.0"` sender. Only
  flip it after follow-up (a).
- Field paths can contain caller-chosen keys; they go back only to that
  caller, and the log uses `%r` so a key with a newline cannot forge a line.

## Verification

- New tests in `tests/test_spec_ingest_workspace.py`: a valid contract passes
  in enforce mode (persisted, no warning); an invalid one raises in enforce
  mode and leaves no spec dir; the same one in warn mode is persisted with a
  warning naming the field; enforce mode with no contract behaves as today;
  an unknown mode value enforces.
- Unit test of `validate_task_contract` returns field paths and no values.
- Existing ingest, specs-route and schema-drift tests pass.

---
status: approved
issue: 1431
intent: intent/2026-10-24-1431-contract-sender-schema.md
---

# Spec: schema-tested proof-run ingest template (#1431)

## Design

1. New file `scripts/proof-run-ingest.example.json`. It is a complete
   `POST /api/specs/ingest` body with placeholder values:
   `project_id: "<tfactory-project-id>"`, `spec_id: "postdeploy-MMDD-<slug>"`,
   a short `spec_text` with an `## Acceptance Criteria` section,
   `format: "markdown"`, `target_paths: []`, and this `contract`:

   ```json
   {
     "contract_version": "2",
     "feature": "post-deploy proof run",
     "workflow_type": "simple",
     "phases": [{"phase": 1, "name": "verify",
                 "subtasks": [{"id": "1.1", "description": "verify the acceptance criteria"}]}],
     "environment": {"language": "python",
                     "provisioning": {"method": "nix", "generated": false}}
   }
   ```

   The values are fixed and come from what a proof run already knows: a
   proof run has one verify step, and the `environment` block is unchanged
   from the bodies used so far. This contract was checked by hand against
   `validate_task_contract` on `origin/dev` and returns `[]`. The current
   `"1.0"` body returns `['<root>: required', 'contract_version: enum']`.
2. `tests/test_task_contract.py`: add one test that loads the example,
   asserts `validate_task_contract(body["contract"]) == []`, and asserts that
   the contract carries the ingest marker (`contract_version`), so the
   template really is validated and persisted by ingest
   (`agents/tools_pkg/tools/task_control.py` around line 507).
3. `docs/capabilities-and-usage.md`, section 7 (around line 198): one
   sentence after the `contract` bullet. It points proof runs and manual
   ingests at the example and says a contract must pass the vendored schema
   (`warn` today, `enforce` later).

## Alternatives rejected

- Change AIFactory's `build_ingest_payload`. It does not produce the failing
  contracts. Its marker-less thin contract is never validated or stored.
- Change PFactory's `build_task_contract`. It already emits and validates
  schema-valid v2 contracts.
- A Python proof-run sender script. This is more code than a template and a
  test, and the bodies are posted from a pod shell anyway.
- Flip `TFACTORY_CONTRACT_SCHEMA_MODE` to `enforce` now. The brief keeps the
  flip out of this lane.
- Integer `contract_version`. The schema enum is the strings `"1"` and `"2"`.

## Risks

- The template drifts from the schema when the hub schema adds a required
  field. The new test fails in that case, which is the point.
- Operators keep copying an old body. The docs line reduces this but cannot
  rule it out. In warn mode such a body only logs a warning.
- None at runtime: the change touches no code path.

## Verification

- `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_task_contract.py -q`
  passes, and the new test fails when `contract_version` is set to `"1.0"`.
- `python scripts/check_schema_drift.py` is unaffected (schema untouched).
- After release, the next proof run's stored `context/task_contract.json`
  validates with no warning.

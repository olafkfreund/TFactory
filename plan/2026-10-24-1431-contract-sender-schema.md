---
status: draft
issue: 1431
spec: spec/2026-10-24-1431-contract-sender-schema.md
---

# Plan: schema-tested proof-run ingest template (#1431)

## Decisions (carried from the approved spec)

- The failing contracts come from no code sender. The 5 `"1.0"` ones are
  hand-copied `postdeploy-*-nix` proof-run ingest bodies, and the 2 without
  `phases` are older hand-posted verify specs. So the fix is to make the body
  a checked-in template and test it against the vendored schema.
- `contract_version` is the string `"2"`, because the schema enum is the
  strings `"1"` and `"2"`, not integers.
- `feature`, `workflow_type` and `phases` take fixed values a proof run
  already implies: `"post-deploy proof run"`, `"simple"`, and one verify
  phase with one subtask. The `environment` block is kept as it is.
- No change to runtime code, AIFactory or PFactory. The
  `TFACTORY_CONTRACT_SCHEMA_MODE` default stays `warn`.

## Steps

`PY=/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/python`. Run
the commands from the worktree root. The worktree has no venv of its own.

1. New file `scripts/proof-run-ingest.example.json`: the full ingest body.

   ```json
   {
     "project_id": "<tfactory-project-id>",
     "spec_id": "postdeploy-MMDD-<slug>",
     "spec_text": "# Ordinal\n\n## Acceptance Criteria\n\n1. `ordinal(1)` returns `\"1st\"`.\n2. `ordinal(-1)` raises `ValueError`.\n",
     "format": "markdown",
     "target_paths": ["src/app/ordinal.py"],
     "contract": {
       "contract_version": "2",
       "feature": "post-deploy proof run",
       "workflow_type": "simple",
       "phases": [{"phase": 1, "name": "verify",
                   "subtasks": [{"id": "1.1", "description": "verify the acceptance criteria"}]}],
       "environment": {"language": "python",
                       "provisioning": {"method": "nix", "generated": false}}
     }
   }
   ```

   → verify: `$PY -c "import json;json.load(open('scripts/proof-run-ingest.example.json'))"`
   Traps: placeholders only, with no real project id, host or name (house
   rule). The file must be valid JSON, so no comments. The json-lint and
   prettier pre-commit hooks may reformat it; accept their formatting.

2. `tests/test_task_contract.py`: append after line 134. Add
   `from pathlib import Path` and import `validate_task_contract` into the
   existing `from agents.task_contract import (...)` block (lines 11-16).

   ```python
   _PROOF_RUN = Path(__file__).resolve().parents[1] / "scripts" / "proof-run-ingest.example.json"


   def test_proof_run_template_contract_is_schema_valid():
       # #1431: the hand-copied proof-run body was the sender of "1.0" contracts.
       contract = json.loads(_PROOF_RUN.read_text())["contract"]
       assert "contract_version" in contract  # ingest validates and persists it
       assert validate_task_contract(contract) == []
   ```

   → verify: `$PY -m pytest tests/test_task_contract.py -q` passes. Then set
   `"contract_version": "1.0"` in the example, run it again, and see it fail.
   Revert that change.
   Traps: tests run from the repo root (the pattern
   `Path(__file__).resolve().parents[1]` is the same one
   `tests/test_cli_build_imports_resolve.py:31` uses). The spec's
   `cd apps/backend` command is wrong for this repo; this command
   supersedes it. Do not assert on `error.message` (#718).

3. `docs/capabilities-and-usage.md`, section 7, after the HTTP bullet that
   ends at line 201 ("The auto-pipeline ... runs on ingest."). Add one
   indented sentence:
   "For a post-deploy proof run or a hand-made ingest, start from
   `scripts/proof-run-ingest.example.json`. A `contract` must pass the
   vendored task-contract schema (`TFACTORY_CONTRACT_SCHEMA_MODE`: `warn`
   today, `enforce` later)."
   → verify: `grep -n proof-run-ingest docs/capabilities-and-usage.md`
   Traps: the markdown lint hook. Keep it to one sentence. Do not touch the
   schema-drift section (line 267 onward).

## Tests

- `$PY -m pytest tests/test_task_contract.py tests/test_schema_drift.py -q`:
  all pass.
- Negative check from step 2: `"1.0"` makes the new test fail.

## Rollback

Revert the commit. It touches only one new JSON file, one test and one docs
sentence, with no runtime effect.

## Follow-ups (not this lane)

- The lead updates the scratchpad proof-run bodies (`ingest*.json`) to the
  template, or copies the template for the next proof run.
- Flip `TFACTORY_CONTRACT_SCHEMA_MODE` to `enforce` once a proof run on the
  template stores a valid contract (#1431 second half).

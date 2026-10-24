---
status: draft
issue: 1427
spec: spec/2026-10-24-1427-contract-schema-gate.md
---

# Plan: Validate the incoming task contract against its schema at ingest

Approved decisions, copied from the spec:

- **D1** `validate_task_contract(contract: dict) -> list[str]` in
  `apps/backend/agents/task_contract.py`: one cached
  `jsonschema.Draft202012Validator` over the vendored
  `apps/backend/contracts/task-contract-v2.schema.json`; returns sorted,
  de-duplicated `"<path>: <validator>"` strings (`/`-joined
  `error.absolute_path`, `<root>` when empty). Never `error.message` (it
  quotes contract values). `[]` = valid. No format checking.
- **D2** Gate in `create_spec_ingest_workspace`, after the `ref` checks and
  before `spec_dir.mkdir`, only for a `dict` with a `tfactory` or
  `contract_version` key. Mode = `os.environ.get("TFACTORY_CONTRACT_SCHEMA_MODE",
  "warn").strip().lower()`. Errors + `warn` → append
  `"task contract fails schema (warn mode): " + "; ".join(errors)` to
  `warnings`, log WARNING with `%r`, continue. Errors + any other value →
  `raise ValueError("task contract fails schema: " + "; ".join(errors))`
  (unknown mode enforces). No contract / no markers / valid → unchanged.
- **D3** `routes/specs.py` already maps `ValueError` to 400; only update its
  comment. MCP caller already returns `ValueError` as an error.
- **D4** Add `jsonschema>=4.18` to `apps/backend/requirements.txt`.
- **D5** Default `warn` (live check: 7 of 14 stored contracts fail). No chart
  change.
- Out of scope: signature verification, fixing the `"1.0"` sender, flipping
  the default. Both get follow-up issues (step 5).

Paths are relative to the repo root. Run Python with the main clone's venv:
`PY=/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/python`
(the worktree has no `.venv`).

## Steps

1. `apps/backend/requirements.txt`:13 (after `PyYAML>=6.0.0`): add a
   one-line comment `# RFC-0002 contract schema gate at ingest (#1427)` and
   `jsonschema>=4.18`.
   → verify: `grep -n '^jsonschema' apps/backend/requirements.txt`
   Traps: none; the image already resolves 4.26.0 transitively, so this
   only declares it.

2. `apps/backend/agents/task_contract.py`: imports at :20-25 add
   `from functools import lru_cache` and `import jsonschema`; after
   `_KNOWN_LANES` (:31-33) add
   `_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "contracts" / "task-contract-v2.schema.json"`,
   an `@lru_cache(maxsize=1)` `_contract_validator()` returning
   `jsonschema.Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))`,
   and `validate_task_contract(contract)` per D1
   (`sorted({f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in _contract_validator().iter_errors(contract)})`).
   Add one sentence to the module docstring that ingest validates against
   the vendored schema (#1427).
   → verify: `cd apps/backend && $PY -c "from agents.task_contract import validate_task_contract as v; print(v({'contract_version':'1.0'}))"`
   prints `['<root>: required', 'contract_version: enum']`.
   Traps: the module docstring says it is "tolerant"; that stays true for
   reading. Do not touch `read_task_contract` or `parse_tfactory_profile`.

3. `apps/backend/agents/tools_pkg/tools/task_control.py`:497-500: between
   the `_REF_RE` check and `spec_dir.mkdir(...)` insert the D2 gate. Import
   `validate_task_contract` locally inside the function
   (`from agents.task_contract import validate_task_contract`), next to the
   existing local `spec_sources` import at :472. Log with the module's
   existing pattern (`import logging as _lg`, as at :684) and
   `_lg.getLogger(__name__).warning("task contract fails schema for %r: %r", spec_id, errors)`.
   Add `ValueError` reason "the contract fails the schema in enforce mode" to
   the docstring's Raises at :468-470, and name the env var there.
   → verify: `$PY -m pytest tests/test_spec_ingest_workspace.py -q` (existing
   tests still pass under the warn default).
   Traps: the gate must run before `spec_dir.mkdir` (parse-before-create is a
   tested property, `test_no_criteria_raises_and_leaves_no_dir`). Keep the
   marker predicate identical to :512-514, do not widen it to every dict.
   Do not put `exc`/contract values into the message (#718 rule in the
   comment at :484-490).

4. `apps/web-server/server/routes/specs.py`:335-343: extend the comment's
   list of verified `ValueError` raise sites with "the #1427 contract schema
   gate (field paths and validator names from the caller's own contract)".
   Comment only, no code.
   → verify: `cd apps/web-server && $PY -m pytest tests/test_specs_route.py -q`
   Traps: none.

5. Tests, append to `tests/test_spec_ingest_workspace.py` after
   `test_no_contract_means_inference` (:368), using `monkeypatch.setenv` and
   this valid fixture:
   `{"contract_version": "2", "feature": "f", "workflow_type": "feature", "phases": [{"phase": 1, "name": "p", "subtasks": [{"id": "1.1", "description": "d", "status": "pending"}]}], "tfactory": {"lanes": ["unit"]}}`
   (checked valid against the schema on 2026-10-24).
   - `test_valid_contract_passes_in_enforce_mode`: enforce; persisted; no
     warning starting with `task contract`.
   - `test_invalid_contract_rejected_in_enforce_mode`: enforce; fixture
     without `phases` → `pytest.raises(ValueError, match="phases")`; spec dir
     does not exist.
   - `test_invalid_contract_warned_by_default`: env unset
     (`monkeypatch.delenv(..., raising=False)`); same invalid contract is
     persisted and `result["warnings"]` has an entry containing `phases`.
   - `test_unknown_mode_fails_closed`: mode `enforced` → raises.
   - `test_enforce_without_contract_unchanged`: enforce, no contract → no
     raise, no `task_contract.json`.
   - `test_validate_returns_paths_not_values`: `validate_task_contract({"contract_version": "SECRET"})`
     contains `contract_version: enum` and no element contains `SECRET`.
   → verify: `$PY -m pytest tests/test_spec_ingest_workspace.py tests/test_schema_drift.py -q`
   Traps: `_ingest` passes `schedule=False`; keep it. Tests run from the
   repo root.

6. After the PR is open (not a code step): file two TFactory issues and link
   them in the PR. (a) "Contract sender posts contract_version 1.0 without
   feature/workflow_type/phases; fix it, then default
   TFACTORY_CONTRACT_SCHEMA_MODE to enforce (#1427)". (b) "verify the
   contract approval signature with the signer's public key (TFactory
   verifies; AIFactory does not re-sign)". The PR body lists the failing
   live contracts by project id and field only (table in the intent), no
   content.
   → verify: `gh issue list -R olafkfreund/TFactory --search 1427`
   Traps: no names, IPs or contract content in issues or PR.

## Tests

- `$PY -m pytest tests/test_spec_ingest_workspace.py tests/test_schema_drift.py tests/test_correlation_sync.py tests/test_fleet_provider_propagation.py tests/test_specs_ingest_clone.py -q` → all pass.
- `cd apps/web-server && $PY -m pytest tests/test_specs_route.py -q` → pass.
- Commit with hooks on (ruff, critical lane). Hooks can take 10+ minutes.

## Rollback

Revert the PR. Operational rollback without a deploy: the default is
already `warn`; if enforce was turned on, set
`TFACTORY_CONTRACT_SCHEMA_MODE=warn` and restart the pod.

---
status: draft
issue: 1713
spec: spec/2026-11-27-1713-ingest-correlation-key.md
coders:
  1: agy     # small mechanical field + pass-through across two files (owner: agy codes in the team)
reviewers:
  - codex    # independent read of validation and precedence
  - agy      # second model (agent-team)
---

# Plan: a spec ingest can carry the run's correlation key

## Approved decisions

The spec, copied here so this plan stands alone.

- **Request field:** `apps/web-server/server/routes/specs.py` `SpecIngestRequest` (~70–165) gains
  `correlation_key: str | None = Field(default=None, max_length=128,
  pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$")`, plus a `field_validator` that raises
  `ValueError` for an all-digits value. That gives a 422.
- **Pass-through:** `ingest_spec` (~557–567) passes `correlation_key=req.correlation_key`.
- **Workspace:** `apps/backend/agents/tools_pkg/tools/task_control.py`
  `create_spec_ingest_workspace` (~699) gains the keyword `correlation_key: str | None = None`.
  `source["correlation_key"]` (~931) becomes `_handoff_key(contract) or correlation_key`.
- **No change:**
  - `triager.py`, which already reads `source.json`;
  - the MCP-stdio router, which uses the same model;
  - `task_contract.json` handling.

## Steps

1. **agy:** the three edits above.

   **Tests:**
   - in `tests/test_spec_ingest_workspace.py`:
     - the key is stored;
     - a contract key wins;
     - with no key, the value is null;
     - `triager._correlation_key` returns the stored key for that spec dir.
   - in the specs route tests, found with `grep -ln SpecIngestRequest tests/`: an over-length
     key, one with a space, and an all-digits key each return 422.

   **Verify:**
   ```bash
   /mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest -q tests/test_spec_ingest_workspace.py <the route test file> tests/test_triager.py
   ```
   Then run `ruff check` and `ruff format --check` on the changed files.

   **Traps:**
   - The worktree has no venv, so use the main clone's venv.
   - The ratchet sees committed files only. The lead runs it:
     ```bash
     python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
     ```

## Tests

- The verify command above.
- The full non-slow suite, compared with an `origin/dev` baseline.
- CI green.

## Rollback

Revert the PR. The field disappears, and callers that send it get a 422 for an unknown field,
but only if the model forbids extras. Check this before release.

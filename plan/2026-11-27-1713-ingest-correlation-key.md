---
status: approved
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

- **Request field:** `apps/web-server/server/routes/specs.py` `SpecIngestRequest` (~70–177) gains
  `correlation_key: str | None = Field(default=None, max_length=128,
  pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$")`, plus a `field_validator` that raises
  `ValueError` when the value is not `None` and is all digits. That gives a 422.
- **Pass-through:** `ingest_spec` (~557–567) passes `correlation_key=req.correlation_key`.
- **Workspace:** `apps/backend/agents/tools_pkg/tools/task_control.py`
  `create_spec_ingest_workspace` (~699) gains the keyword `correlation_key: str | None = None`.
  `source["correlation_key"]` (~933) becomes `_handoff_key(contract) or _handoff_key({"correlation_key": correlation_key})`.
  That reuses the strip/blank rule, so `""` and whitespace store `null` (agy review).
- **No change:**
  - `triager.py`, which already reads `source.json`;
  - the MCP-stdio router, which uses the same model;
  - `task_contract.json` handling.

## Steps

1. **agy:** the three edits above.

   **Tests:**
   - in `tests/test_spec_ingest_workspace.py`:
     - the key is stored;
     - a contract key wins, using a contract **without** RFC-0002 markers, so the workspace fallback
       is exercised;
     - with no key, `""` or whitespace, the value is null;
     - `triager._correlation_key` returns the stored key for that spec dir.
   - in `tests/test_specs_ingest_clone.py` (direct route) and `tests/test_mcp_stdio_authz.py`
     (mcp-stdio proxy), following their existing ingest fixtures:
     - a **successful** ingest with `correlation_key="o/r#42"` or `"pf-337-x"` writes it to
       `source.json` (Codex and agy review: otherwise a missing pass-through goes undetected);
     - an over-length key, one with a space, and an all-digits key each return 422.

   **Verify:**
   ```bash
   /mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest -q tests/test_spec_ingest_workspace.py tests/test_specs_ingest_clone.py tests/test_mcp_stdio_authz.py tests/test_triager.py
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

Revert the PR. `SpecIngestRequest` doesn't forbid extra fields (Pydantic's default is ignore), so
callers that still send `correlation_key` are accepted. The key is silently dropped, and verify
events go back to `tf-…` (Codex and agy review).

## Review (plan draft, 2026-11-27)

Codex (gpt-6.2-sol) and agy (model not reported) reviewed the draft. Both were read-only, with no
changes.

| Finding | Verdict | Change |
|---|---|---|
| The rollback note is wrong: extras are ignored, not rejected with 422 (both) | confirmed (`specs.py:70`, no `model_config`) | Rollback rewritten |
| No route-level pass-through test (both) | confirmed | a successful ingest test on each route |
| An empty or whitespace key is stored as is (agy) | confirmed (`task_control.py:933`) | normalised via `_handoff_key` |
| The digits validator needs a `None` guard (agy) | confirmed | done |
| Anchors drifted, and the grep doesn't recurse (Codex) | confirmed | anchors and test files named |

## Deviations (diff review, 2026-11-27)

- **Fresh Opus review (medium):** a `mode="before"` validator turns an empty or whitespace
  `correlation_key` into `None`. Before, it got a 422 over HTTP. There is a test in
  `tests/test_specs_ingest_clone.py`.
- **Accepted, not fixed:**
  - a project member can send any valid key, the same as a handoff contract could before;
  - a contract's own key isn't re-checked against the new pattern.

  Both behaved the same before this change. They are noted for a follow-up.
- **agy said "tests missing":** refuted. The diff I gave agy covered `apps/` only. The tests are
  in `tests/`, and 186 pass.

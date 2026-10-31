---
status: approved
issue: 1480
spec: spec/2026-10-31-1480-copilot-mcp-fail-closed.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
---

# Plan: copilot MCP endpoint fails closed without its token

Approved decisions: `/mcp` answers 503 on every request when
`COPILOT_MCP_TFACTORY_TOKEN` is unset (route stays mounted); bearer compared
with `hmac.compare_digest`; tools take `task_id` as `project_id:spec_id`, bare
or unsafe → HTTP 400, path `<root>/workspaces/<project_id>/specs/<spec_id>`,
no cross-project scan. Prod does not set the token: the PR must say so.

## Steps

1. `apps/web-server/server/routes/mcp_copilot.py`: `_check_auth` → 503 when
   unset, `hmac.compare_digest` otherwise; replace `_find_spec_dir` with
   `_resolve_spec_dir` (400 via `HTTPException` on a bare/unsafe id, `None`
   when `status.json` is missing); every tool calls it; `tools/call` re-raises
   `HTTPException` before the generic `except`; tool schemas and module
   docstring describe `project_id:spec_id` and the 503; update the
   `COPILOT_MCP_TFACTORY_TOKEN` row in `docs/environment-reference.md`.
   → verify by `ruff check` + `ruff format --check` on the file.
   Traps: do not touch `routes/tasks_worktree.py` (#1479); keep `is_safe_slug`
   from `._specpath`.
2. Tests: `tests/test_github_agentic_integration.py` fixtures set a token and
   send the header, task ids become `proj:spec-…`; add token-unset → 503 and
   bare-id → 400. `apps/web-server/tests/test_id_traversal_guards.py`: the
   mcp test calls `_resolve_spec_dir` with the evil value in each part and
   expects `HTTPException` 400.
   → verify by `pytest tests/test_github_agentic_integration.py
   apps/web-server/tests/test_id_traversal_guards.py -q`.
   Traps: new cases must fail on dev.

## Tests

The two pytest files above pass; the 503 and bare-id cases fail on origin/dev.

## Rollback

Revert the PR. Or set `COPILOT_MCP_TFACTORY_TOKEN` in prod to bring `/mcp` back.

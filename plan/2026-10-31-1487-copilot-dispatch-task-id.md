---
status: approved
issue: 1487
spec: spec/2026-10-31-1487-copilot-dispatch-task-id.md
coders:
  1: agy   # the lead named agy as coder for this lane
---

# Plan: Copilot dispatch prompt names the MCP task id

Decisions (from the approved spec): `dispatch_test_writing` takes required
keyword args `project_id`, `spec_id`; builds `task_id = f"{project_id}:{spec_id}"`;
the issue body and the `@copilot` prompt both contain it and name it as the
`task_id` argument for the `tfactory_get_*` tools. No path parsing.

## Steps

1. apps/backend/agents/copilot_dispatch.py: add `project_id: str, spec_id: str`
   keyword args to `dispatch_test_writing` (~74) and its docstring; pass
   `task_id` to `_ensure_issue` (~169, body ~181) and `_assign_copilot`
   (~202, prompt ~218-226), which put it in their texts. Update the existing
   call in tests/test_github_agentic_integration.py (~126) with the new args.
   Add a test there that patches `subprocess.run`, calls `_ensure_issue` and
   `_assign_copilot` with `"proj-1:spec-2"`, asserts both bodies contain it and
   `task_id`, and asserts both halves pass `server.routes._specpath.is_safe_slug`.
   → verify by `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_github_agentic_integration.py -q`
   Traps: never `git add -A` (the `.venv` symlink); ruff on apps/backend.

## Deviations

- Step 1: a sixth `task_id` arg on `_assign_copilot` tripped the CI ratchet
  (PLR0913, max 5 args). Prompt building moved to
  `_build_prompt(task_id, lanes, frameworks, ac_map) -> str`, and
  `_assign_copilot(repo_full_name, issue_number, prompt_body)` now only posts
  and assigns. Same prompt text. The test calls both.

## Tests

`pytest tests/test_github_agentic_integration.py` green; ruff clean.

## Rollback

Revert the commit.

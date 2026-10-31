---
status: approved
issue: 1480
intent: intent/2026-10-31-1480-copilot-mcp-fail-closed.md
---

# Spec: copilot MCP endpoint fails closed without its token

## Design

All in `apps/web-server/server/routes/mcp_copilot.py`:

1. `_check_auth`: token unset/blank → `HTTPException(503, "Copilot MCP endpoint
   is not configured")`. Otherwise compare the bearer with
   `hmac.compare_digest` on UTF-8 bytes; mismatch or missing → 401 as today.
   The route stays mounted (`main.py` unchanged); `/.well-known` keeps
   advertising it.
2. Replace `_find_spec_dir(task_id)` with `_resolve_spec_dir(task_id)`: split on
   the first `:` into `project_id`, `spec_id`; either part missing or failing
   `is_safe_slug` → `HTTPException(400)`. Path is
   `<root>/workspaces/<project_id>/specs/<spec_id>`, the same layout as
   `badges.py` and `handback.py`; it returns `None` (tool answers
   "task not found") when `status.json` is absent.
3. `tools/call` dispatch re-raises `HTTPException` so the 400 reaches the
   client instead of becoming JSON-RPC -32603.
4. Tool `inputSchema` descriptions say `task_id` is `project_id:spec_id`;
   module docstring and `docs/environment-reference.md` say unset → 503.

## Alternatives rejected

- Do not mount the router when the token is unset: the decision is read at
  import time, harder to test, and a 404 hides a misconfiguration that a 503
  names. Rejected by the user in favour of 503 on every request.
- Keep the bare-id scan behind a project hint: still returns another project's
  spec on a collision. Rejected.
- JSON-RPC error (-32602) for a bare id instead of HTTP 400: the user chose 400,
  matching the other routes' `safe_slug` failures.

## Risks

- Prod does not set `COPILOT_MCP_TFACTORY_TOKEN` (factory-gitops name grep), so
  `/mcp` becomes 503 in prod. No caller of `/mcp` exists in the TFactory,
  Factory or gitops repos; a Copilot repo-settings MCP config, if any, lives in
  GitHub settings and cannot be checked from the repo.
- A Copilot config passing bare spec ids now gets 400.

## Verification

`tests/test_github_agentic_integration.py`: token unset → 503 for `initialize`;
bare `task_id` → 400; `proj:spec` → resolved; wrong token 401; right token 200.
`apps/web-server/tests/test_id_traversal_guards.py`: traversal in either part →
400. Both fail on dev for the new cases.

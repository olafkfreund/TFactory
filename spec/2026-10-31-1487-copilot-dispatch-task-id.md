---
status: approved
issue: 1487
intent: intent/2026-10-31-1487-copilot-dispatch-task-id.md
---

# Spec: Copilot dispatch prompt names the MCP task id

## Design

User decision: exactly the issue's fix.

- `dispatch_test_writing` gains required keyword args `project_id` and
  `spec_id`. There is no production caller yet (only a test), so nobody
  breaks. A caller gets both from the spec's `status.json`, the same way
  `agents/triager.py` does (`status.get("project_id")`, `status.get("spec_id")`).
- It builds `task_id = f"{project_id}:{spec_id}"` and passes it to
  `_ensure_issue` (issue body) and `_assign_copilot` (prompt comment).
- Both texts name it as the `task_id` argument for the `tfactory_get_*` tools.

## Alternatives rejected

- Derive the ids from `spec_dir` path segments: the lead ruled out parsing
  paths; layout may change and it hides a wrong id.
- Read `status.json` inside dispatch: adds file I/O and a failure mode for a
  value the caller already has.

## Risks

Low. Prompt text only; the signature change has no production callers.

## Verification

New pytest: the posted prompt and the issue body contain the full id and the
`task_id` wording, and the id passes `mcp_copilot`'s check (`is_safe_slug` on
both halves, same rule `_resolve_spec_dir` applies).

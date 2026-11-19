---
status: draft
issue: 1624
spec: spec/2026-11-19-1624-tf-go-test-paths.md
coders:
  1: codex
reviewers:
  - codex
---

# Plan: Go test paths and unreadable-source handling

Carries every decision of spec/2026-11-19-1624-tf-go-test-paths.md (approved, owner advance approval 2026-11-19; intent approved likewise). Decision (owner delegation 2026-11-19): recommended option from the issue's own fix idea.

## Steps

1. apps/backend/agents/triager.py (_resolve_test_source, _build_candidates, _run_git_side_effect `c.source.strip()`) and apps/backend/agents/triage_dedup.py (blank sources bypass dedup) exactly as in the spec Design. Step 1 is small tricky logic, so codex implements it; the lead reviews and commits.
2. Add the tests named in the spec's Verification section to tests/test_triager_go_paths.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: python -m pytest tests/test_triager_go_paths.py tests/test_triager.py tests/test_triage_dedup.py tests/test_triager_delivery_honesty.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

python -m pytest tests/test_triager_go_paths.py tests/test_triager.py tests/test_triage_dedup.py tests/test_triager_delivery_honesty.py -q; python scripts/ratchet_lint.py for both apps.

## Rollback

Revert the commit; no data or config migration.

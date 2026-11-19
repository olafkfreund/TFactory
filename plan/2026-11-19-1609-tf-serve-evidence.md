---
status: draft
issue: 1609
spec: spec/2026-11-19-1609-tf-serve-evidence.md
coders:
  1: codex
reviewers:
  - codex
---

# Plan: Infer serve targets from the spec text

Carries every decision of spec/2026-11-19-1609-tf-serve-evidence.md (approved, owner advance approval 2026-11-19; intent approved likewise). Decision (owner delegation 2026-11-19): infer from spec text because ingest carries no target_paths; only script suffixes.

## Steps

1. apps/backend/agents/nix_env.py: add _spec_text_targets and _browser_targets exactly as in the spec Design, and call _browser_targets from run_browser_evidence in place of the inline source.json read (around nix_env.py:2186-2194). Small tricky regex logic, so codex implements it; the lead reviews and commits.
2. Add the tests named in the spec's Verification section to tests/test_serve_command_node_subdir.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: python -m pytest tests/test_serve_command_node_subdir.py tests/test_nix_env.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

python -m pytest tests/test_serve_command_node_subdir.py tests/test_nix_env.py -q; python scripts/ratchet_lint.py for both apps.

## Rollback

Revert the commit; no data or config migration.

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

## Decisions (copied from the approved spec)


nix_env.py adds `_spec_text_targets(spec_dir, project_dir) -> list[str]`: read context/aifactory_spec.md and match whole repo-relative tokens with the regex `(?<![\w./~-])((?:[\w][\w.-]*/)*[\w][\w.-]*\.(?:js|mjs|cjs|ts))(?![\w-]|\.\w|/)`, keep those that exist as files whose resolved path stays inside project_dir, dedupe, cap 20. Only script suffixes are inferred, so an inferred html page cannot flip detection into the static-page branch. A new `_browser_targets(spec_dir, project_dir)` returns source.json target_paths when non-empty, else the inferred list or None; run_browser_evidence calls it in place of its inline source.json read. detect_serve_command is unchanged, so its node-subdir rule serves `PORT=<port> node scoreboard/server.js`; serve_command in the evidence is the command the Job ran, so it is correct once the right one is chosen.

Test matrix: tests/test_serve_command_node_subdir.py: (1) _browser_targets with no source targets and spec text naming scoreboard/server.js and scoreboard/index.html returns only the .js, and detect_serve_command with it gives the node command; (2) explicit source.json targets take precedence; (3) spec text with no paths, a nonexistent file, ../scoreboard/server.js, /scoreboard/server.js, scoreboard/server.json: none inferred. Fixtures contain "listen" so removing a path guard breaks them. (1) fails on current dev.

## Steps

1. apps/backend/agents/nix_env.py: add _spec_text_targets and _browser_targets as in Decisions above, and call _browser_targets from run_browser_evidence in place of the inline source.json read (around nix_env.py:2186-2194). Small tricky regex logic, so codex implements it; the lead reviews and commits.
2. In tests/test_serve_command_node_subdir.py give the empty-server fixtures at lines ~40 and ~48 a body containing `listen` so the path guards are what reject them.
3. Add the Test matrix above to tests/test_serve_command_node_subdir.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: python -m pytest tests/test_serve_command_node_subdir.py tests/test_nix_env.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

python -m pytest tests/test_serve_command_node_subdir.py tests/test_nix_env.py -q; from the repo root, after committing: python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server.

## Rollback

Revert the commit; no data or config migration.

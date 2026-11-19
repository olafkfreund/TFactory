---
status: approved
issue: 1609
spec: spec/2026-11-19-1609-tf-serve-subdir.md
coders:
  1: lead   # small contained edit, kept by the lead
  2: lead   # small contained edit, kept by the lead
reviewers:
  - codex    # read-only review of each draft and the final diff
---

# Plan: Serve the node app nearest the spec targets (#1609)

Approved decisions (spec): nix_env.py `_node_subdir_serve`: for each target, walk its parent directories (plain `[\w][\w.-]*` path parts only: the command is interpolated into the Job's bash script, Codex review) from the deepest, return the first with server.js or index.js whose text contains `listen` (a browser entry such as React src/index.js is not a server, final Codex review). Called first inside the non-static branch of detect_serve_command, so python and root npm detection are unchanged when no node entry is near a target. The env-prefix form needs no shell metacharacter. Rejected: running the subdir package.json `start` (needs `cd`/`&&`, which the safe-command rule forbids) and skipping root `npm start` for unrelated apps (not needed once the right app is found; noted as follow-up).

## Steps

1. apps/backend/agents/nix_env.py: _node_subdir_serve + call in detect_serve_command -> verify by `pytest tests/test_serve_command_node_subdir.py tests/test_serve_command_polyglot.py`
   Traps: none
2. tests/test_serve_command_node_subdir.py -> verify by `fails on origin/dev`
   Traps: none

## Tests

Run the named pytest files with apps/backend/.venv/bin/pytest; then `scripts/ratchet_lint.py` for apps/backend and apps/web-server.

## Rollback

Revert the PR; no data or config changes.

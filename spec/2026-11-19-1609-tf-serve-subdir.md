---
status: approved
issue: 1609
intent: intent/2026-11-19-1609-tf-serve-subdir.md
---

# Spec: Serve the node app nearest the spec targets

## Design

nix_env.py `_node_subdir_serve`: for each target, walk its parent directories (plain `[\w][\w.-]*` path parts only: the command is interpolated into the Job's bash script, Codex review) from the deepest, return the first with server.js or index.js. Called first inside the non-static branch of detect_serve_command, so python and root npm detection are unchanged when no node entry is near a target. The env-prefix form needs no shell metacharacter. Rejected: running the subdir package.json `start` (needs `cd`/`&&`, which the safe-command rule forbids) and skipping root `npm start` for unrelated apps (not needed once the right app is found; noted as follow-up).

## Alternatives rejected

See Design.

## Risks

Only specs with targets are affected; repo-wide callers pass no targets.

## Verification

Unit tests built on the real layout that fail on origin/dev and pass with the change; ratchet_lint for apps/backend and apps/web-server; live re-verify after release 0.13.1 (owner/coordinator).

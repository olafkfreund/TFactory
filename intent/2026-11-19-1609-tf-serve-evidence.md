---
status: draft
issue: 1609
author: olafkfreund
---

# Intent: Browser lane serves the node SUT, and says so

## Problem

aifactory-demo #625 passed VAL-2 but browser_evidence.serve_command read 'python -m uvicorn app.main:app'. serve_command is the exact command the Job ran, so the Job script was built with the wrong server command (startup success is not proven by the field): ingest had no target_paths, detect_serve_command went repo-wide and found src/app/main.py before the node app.

## Proposed outcome

With no target_paths, targets are inferred from repo files named in the spec text, so the node app under scoreboard/ is served with PORT set.

## Affected users and systems

apps/backend/agents/nix_env.py run_browser_evidence.

## Constraints

Smallest correct change; tests that fail without the fix; no cluster changes; base dev; release lead ships 0.13.3.

## Open questions

None. Decision (owner delegation 2026-11-19): infer targets from existing repo paths mentioned in context/aifactory_spec.md when source.json has none; detection is unchanged otherwise.

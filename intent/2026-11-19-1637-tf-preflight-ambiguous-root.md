---
status: approved
issue: 1637
author: olafkfreund
---

# Intent: Pre-flight must not reject imports that exist in a same-named sibling package

## Problem

When a checkout has two top-level packages with the same name (aifactory-demo PR #624: `src/app` and `services/notes-api/app`), the pre-flight probe imports only the first, finds the name missing, and reports a hallucination. The subtask replans until it is stuck.

## Proposed outcome

An import is rejected only when no copy of the package in the checkout provides the name.

## Affected users and systems

`apps/backend/agents/preflight_static.py` `check_import`; caller `gen_functional.py`. Verify of any monorepo with a repeated package name.

## Constraints

Keep the hallucination guard for single-root packages and third-party/stdlib modules. No cluster actions.

## Open questions

None.

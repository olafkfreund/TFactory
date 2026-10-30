---
status: approved
issue: 1467
spec: spec/2026-10-30-1467-repo-scoped-correlation-key.md
---

# Plan: never emit a bare issue number as the correlation key

Decision (approved spec): `<repo>#<n>` when the repo is known, else synthetic
`tf-<spec_id>`; never a bare number. No repo derivation.

## Steps

1. `apps/backend/agents/triager.py:743-761` `_correlation_key`: when the issue is
   known but `_source_repo(source)` is empty, fall through to the `tf-<spec_id>`
   return; update the docstring. `apps/backend/agents/handback/send.py:84-113`
   `_correlation_key_for`: without a repo return
   `f"tf-{source.get('spec_id') or spec_dir.name}"`; update the docstring.
   In `tests/test_correlation_sync.py` change `test_correlation_falls_back_to_issue_number`
   (expect `tf-<spec dir name>`), rename `test_correlation_without_repo_stays_bare`
   to `..._is_synthetic` (no bare number), and `test_handback_key_from_source_issue`
   (expect `tf-<spec dir name>`).
   → verify by `python -m pytest tests/test_correlation_sync.py tests/test_docs_emit_341.py -q`
   Traps: grep tests for other bare-key asserts (`== "42"`, `== "55"`).

## Tests

Pytest above green; ruff clean.

## Rollback

Revert the PR commit.

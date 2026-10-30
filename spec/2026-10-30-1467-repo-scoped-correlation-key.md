---
status: draft
issue: 1467
intent: intent/2026-10-30-1467-repo-scoped-correlation-key.md
---

# Spec: never emit a bare issue number as the correlation key

## Design

- `_correlation_key`: an issue with a repo gives `<repo>#<n>`; an issue
  without one falls through to the existing synthetic `tf-<spec_id>`.
- `handback/send.py` `_correlation_key_for`: same rule; without a repo it
  returns `tf-<spec_id>` (`source.spec_id`, else the spec dir name, as the
  triager falls back), so the hand-back and the event share one key. No issue
  stays None, as today.

## Alternatives rejected

- Derive the repo from the project's configured repo: TFactory's project need
  not be the issue's repo (AIFactory handoffs and PFactory pickups name it per
  task), so a guess can misattribute the work item.
- Raise when the repo is missing: completion must never break on the key.

## Risks

A run that relied on the bare key now gets `tf-<spec_id>`; bare keys already do
not join after CFactory #506. AIFactory now sends `af-<spec_id>` as the handoff
key in this case (AIFactory #1960), which wins over this fallback.

## Verification

`tests/test_correlation_sync.py`: missing repo yields `tf-<spec_id>` in both
the triager and the hand-back; repo-scoped cases unchanged.

---
status: approved
issue: 1467
author: olafkfreund
---

# Intent: never emit a bare issue number as the correlation key

## Problem

`_correlation_key()` in `apps/backend/agents/triager.py` returns `str(issue)`
when `source.json` names no repo, and the hand-back key
(`apps/backend/agents/handback/send.py` `_correlation_key_for`) does the same.
Since CFactory #506 keys match exactly, so issue 7 in two repos would share one
CFactory work item.

## Proposed outcome

TFactory never emits a bare number. An issue with no known repo correlates on
the synthetic `tf-<spec_id>`, in the completion event and the hand-back alike.

## Affected users and systems

TFactory completion events, docs-emit trigger, task rows, hand-back to AIFactory.

## Constraints

Contract and handoff keys keep precedence; `<repo>#<n>` unchanged; no CFactory change.

## Open questions

None.

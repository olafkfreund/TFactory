---
status: draft
issue: 1342
author: olafkfreund
---

# Intent: the liveness sweep summary does not say what "stalled" means

## Problem

The web-server liveness sweep logs
`liveness sweep: 6 task(s) checked, 0 stalled`
(`apps/web-server/server/background/liveness_sweep.py:44-46`). The bar behind
"stalled" is `status.json` `updated_at` idle longer than the deadline
(`apps/backend/agents/liveness.py:69`, default 900 s, override
`TFACTORY_STALL_DEADLINE_SECONDS`), skipped while a backing Job is active. An
operator watching an 8-minute pause sees "0 stalled" and cannot tell whether
the watchdog is working or what threshold it uses.

## Proposed outcome

The summary line names the deadline in force and the longest idle time seen,
so "0 stalled" can be read against the bar.

## Affected users and systems

Operators reading web-server logs. Log text only; no behaviour change.

## Constraints

- The stall predicate and the deadline value do not change.

## Open questions

None. Telling "waiting on a model" apart from "session ended" (issue item 3)
is a separate design question and out of scope.

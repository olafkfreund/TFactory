---
status: draft
issue: 1259
author: Olaf Krasicki-Freund
---

# Intent: `lane_progress` must reflect lanes that have run

## Problem

`status.json`'s `lane_progress` read `pending` for all five lanes at a moment
when the run had 27 executed test cases, 13 `junit.xml` files and 6 mutants
on disk. The unit lane was complete and the mutation lane six probes in.

`pending` is wrong in the dangerous direction: an automated caller reading it
concludes verification has not run and waits, or treats a finished run as
incomplete. It is the third status field on this build that looks maintained
and is not (with `phase=None` and the late `human_review` transition).

Current state on `dev`: `_derive_lane_progress` (#1161, 2026-08-25) sets lanes
to `executed`/`error`, but it runs only once, at the end of evaluation, and it
reads `lane` off the verdicts. So:

1. During execution, every lane stays `pending` until the Evaluator finishes.
2. When verdicts carry no `lane` (the #1258 symptom), it returns `None` and
   leaves all lanes `pending` even after the run ends.

## Proposed outcome

- While a lane is running, or once it has produced artifacts, `lane_progress`
  does not report it `pending`.
- After the run, a lane that executed reads `executed` (or `error`), even if
  the verdict-side lane stamp is missing.
- A lane nobody requested still reads `pending`/absent — distinct from
  `executed` and `error`.
- Proven by a mutation-checked test: a run with a completed unit lane must not
  report that lane `pending`.

## Affected users and systems

- `apps/backend/agents/evaluator.py` (`_derive_lane_progress`)
- Initialisers: `agents/tools_pkg/tools/task_control.py:566,796`,
  `agents/handback/rerun.py:104`
- Readers: portal cockpit stage badge, task routes, `/tfactory-watch`, the demo
  runbook, MCP `task_status`
- `findings/_run_artifacts/` and `mutants/` — the sources that were accurate
  throughout the run

## Constraints

- Must not repaint a healthy run as `error`, or a dead run as `executed`.
- Prefer a value that cannot go stale (derived on read from artifacts) over
  more writers — the issue's own suggestion, and #1161 already chose
  derivation over per-call-site stamping.
- No change to the `status.json` shape consumers parse (same keys, same value
  vocabulary), unless the spec argues for it.

## Open questions

- Derive on read (portal/MCP compute it from `_run_artifacts/`) vs. write it
  as lanes finish (Executor updates `status.json`)? The first cannot go stale;
  the second keeps one source for all readers. The spec will recommend one.
- Depends partly on #1258: if the lane stamp is fixed there, point 2 above
  goes away. Sequence #1258 first?

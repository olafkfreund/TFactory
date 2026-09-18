---
status: draft
issue: 1195
author: Olaf Krasicki-Freund
---

# Intent: Mark LLM-authored rationale in the handback artifacts

## Problem

#1195's toolchain, staging and transform defects are fixed; this is the one
item it stays open for. The handback correction that TFactory sends to
AIFactory (`findings/handback_request.{md,json}`) presents the Evaluator
judge's free-text `reasons` as fact:

- `agents/handback/request.py:167` fills each failure's `reason` from the
  judge's `reasons` (`_join_reasons`, `:94`), falling back to `reason` /
  `semantic_relevance` (`:101`).
- `agents/handback/render.py:46` prints it as **`- **Observed:** <reason>`**.

"Observed" is wrong: those sentences are a model's interpretation, not a
measurement. AIFactory's QA Fixer receives `QA_FIX_REQUEST.md` built from
this and acts on it as instructions, so a wrong reason would read with the
same authority as an exit code — and nothing in the artifact lets the reader
tell the two apart. `git grep -i
"LLM-authored|model-authored" -- apps` is empty on `dev`.

## Proposed outcome

- Every piece of model-authored text in the handback artifacts is visibly
  labelled as such in the Markdown and machine-distinguishable in the JSON.
- Measured facts (test id, file, exit status, stability, mutation result,
  coverage) stay unlabelled and are visibly separate from the model's
  rationale.
- A human or the QA Fixer reading `QA_FIX_REQUEST.md` can tell, per line,
  which is which.

## Affected users and systems

- `apps/backend/agents/handback/request.py`, `render.py` (artifact shape)
- AIFactory's handback receiver, which writes `QA_FIX_REQUEST.md` from the
  payload (other repo; `AIFactory#317`) — consumer of the JSON field names
- Operators reading `findings/handback_request.md`; the `/handback-to-aifactory`
  and `/tfactory-fixloop` flows

## Constraints

- The handback JSON is a cross-repo contract: the change must be additive (no
  renamed or removed field) so an AIFactory receiver on the old shape keeps
  working.
- No change to what is *sent* or *when* (dry-run-first, no-automatic-pushes
  policy unchanged).
- Scope is the handback artifacts. The triage report / PR comment also show
  judge reasons; whether they get the same marking is an open question, not
  assumed.

## Open questions

1. Also mark the triage report and PR comment (`triage_report.py`,
   `pr_comment_body.md`), which render the same judge text? Recommend **yes,
   same label** — the PR comment is read by humans as TFactory's finding —
   but it widens the diff to a second renderer.
2. #1195 also once listed "prove a jest lane on a repo with **no** committed
   flake"; no later comment records that run. It needs the deployed cluster,
   not code. Track it on #1195 as a post-deploy check, or drop it?

# triage-and-handback

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: triager,dedup,ranking,triage-report,handback,aifactory,dry-run,completion-event,rfc-0001,fixloop,correlation-key

---

# Triage and Handback

Use this skill when you need to turn Evaluator verdicts into a shipped result — deduping and ranking verdicts into the triage report, understanding the dry-run-by-default git commit / PR comment side-effects, emitting the RFC-0001 completion-event envelope and Backstage scorecard, and driving the fail → handback → AIFactory QA Fixer → bounded re-test loop reconciled on correlation_key. Covers when to flag vs reject vs hand back.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Triage and Handback

The Triager is the last agent in the pipeline and the only one with side-effects. It has no LLM — it's pure compute over the Evaluator's verdicts: drop rejects, dedup the survivors, rank them, render `triage_report.{md,json}`, and (only if explicitly opted in) commit tests + post a PR comment. When tests find real problems, it instead *prepares a handback* to AIFactory, kicking off a bounded fix loop. This skill covers both: producing the report and closing the AIFactory ↔ TFactory loop.

---

## When to use this skill
- Turning `findings/verdicts.json` into a ranked, deduped triage report.
- Understanding why git commit / PR comment default to DRY-RUN and how to opt in.
- Reading/emitting the RFC-0001 completion-event envelope or the Backstage test-quality scorecard.
- Driving the handback loop: failing tests → packaged correction → AIFactory QA Fixer → re-test, bounded by max-cycles, reconciled on correlation_key.
- Deciding, for a given finding, whether to flag, reject, or hand back to AIFactory.
- Do NOT trigger for: computing the verdicts in the first place (test-quality-evaluation), planning which tests to write (contract-driven-test-planning), or cloud posture remediation (cloud-posture-testing).

---

## Key principles
1. **No automatic pushes — dry-run is the default** — the git commit (`tools/git_writer.py`) and PR comment (`tools/pr_comment.py`) only fire when the operator opts in via `TFACTORY_TRIAGER_GIT_WRITE=1` / `TFACTORY_TRIAGER_PR_COMMENT=1`. Side-effects are off until a human says go.
2. **Rejects never reach the report** — the Triager drops every `reject` verdict before deduping. The report is the set of tests worth a human's attention (accepts + flags).
3. **Dedup before rank** — byte-identical and whitespace-normalized duplicates collapse first (`triage_dedup.py`); ranking a deduped set keeps the report honest.
4. **Ranking is deterministic and total** — order by (verdict_priority, mutation, stability, coverage_delta, test_id). Same verdicts → same report, every time.
5. **The loop is bounded** — handback is not infinite. `TFACTORY_HANDBACK_MAX_CYCLES` (default 2) caps correction cycles; exceeding it lands the task in `stuck` for a human.
6. **Everything reconciles on the correlation_key** — the completion envelope's `correlation_id` (the GitHub issue #), the handback request, and the re-test all key off the same task so the loop never crosses wires.
7. **Best-effort callbacks never break the pipeline** — the completion webhook/sentinel and the handback sender are best-effort; a failing target is logged, not fatal.
8. **The Triager has no LLM** — it's deterministic compute. Don't expect it to "judge" anything; all judgment already happened in the Evaluator. The Triager only drops, dedups, ranks, renders, and (opt-in) writes.
9. **A failing test can be a winning test** — if a test is aligned to the AC and fails because the code is wrong, that's the system working. The right response is handback, not reject — don't delete the signal that found the bug.

---

## Core concepts
**The Triager's place in the pipeline** — last of the five agents: `Planner → Gen-Functional → Executor → Evaluator → Triager`. It's the only agent with no LLM prompt — pure compute orchestrating dedup + rank + render + side-effects over the Evaluator's `findings/verdicts.json`. Everything it does is deterministic, which is why the same verdicts always produce the same report.

**TriageCandidate** — a wrapped verdict the Triager operates on. Rejects are dropped; accepts/flags survive into dedup → rank → render.

**Dedup (`triage_dedup.py`)** — collapses tests that are byte-identical or differ only in whitespace, so a generator that emitted the same test twice doesn't inflate the report.

**Ranking key** — `(verdict_priority, mutation, stability, coverage_delta, test_id)`. Higher-value, mutation-killing, stable, coverage-adding tests rise; `test_id` is the deterministic tiebreak.

**triage_report.{md,json}** (`triage_report.py`) — the human-readable + machine-readable renderable report written to `findings/`. When there's no PR# in `source.json`, a `pr_comment_body.md` is written instead of posting.

**Side-effect helpers (dry-run-first)** — `git_writer.py` commits accepted tests to the AIFactory feature branch; `pr_comment.py` runs `gh pr comment --body-file -`. Both no-op (log only) unless their env flag is set.

**RFC-0001 completion-event envelope** — emitted on terminal status (`triaged` / `triaged_empty` / `triager_failed`): `schema_version`, `event`, `service`, `correlation_id`, `outcome` + legacy flat fields. Delivered via `TFACTORY_COMPLETION_WEBHOOK` (POST JSON) and/or `TFACTORY_COMPLETION_SENTINEL=1` (writes `findings/COMPLETED.json`). See `docs/completion-event-envelope.md`.

**Backstage test-quality scorecard** — the verdict/coverage/mutation summary surfaced to Backstage so a service's test health is visible in the catalog.

**Handback (epic #182, `agents/handback/`)** — when a run finishes with failing tests, the Triager *prepares* `findings/handback_request.{md,json}`; the operator *sends* it (`/handback-to-aifactory` or `python -m agents.handback <spec_dir> --send`). AIFactory's receiver writes `QA_FIX_REQUEST.md` onto the original spec and runs its QA Fixer; `/tfactory-fixloop` drives the bounded test → fix → re-test cycle.

**Handback builder/renderer/sender** — the `agents/handback/` package mirrors the dry-run-first discipline: a builder assembles the correction from the failing verdicts, a renderer writes `handback_request.{md,json}`, and a sender (off by default) POSTs to AIFactory. `TFACTORY_HANDBACK_PREPARE` (default ON) builds the artifact; `TFACTORY_HANDBACK_SEND=1` (default OFF) also transmits it; `TFACTORY_AIFACTORY_API_URL` (default `http://localhost:3101`) is the target.

**Terminal statuses** — the Triager ends a task in exactly one of: `triaged` (report produced, candidates present), `triaged_empty` (no candidates survived dedup/reject), or `triager_failed` (the Triager itself errored). The completion-event envelope and any callbacks fire on reaching one of these.

**The direction question (flag/reject/handback)** — these answer different questions. *flag/reject* are about the **test** (keep-with-caveat vs drop). *handback* is about the **code under test** — the tests are right and they're catching a real defect, so the fix belongs upstream in AIFactory. A failing test is not automatically a bad test; if it's aligned to the AC and the code is wrong, it's a successful test that triggers a handback.

**Bounded loop & no-progress detection** — `/tfactory-fixloop` stops on any of three conditions: the re-test passes (done), the *same* tests keep failing across a cycle (no progress — the fixer isn't helping), or `TFACTORY_HANDBACK_MAX_CYCLES` (default 2) is reached → `stuck` for a human. All three are honored on the `correlation_key` so cycles never cross tasks.

---

## Common tasks
### Read a triage report
Open `findings/triage_report.md` (human) or `.json` (machine). Top entries are the highest-value, mutation-killing, stable tests; rejects won't appear (they were dropped pre-report).

### Opt in to git commit + PR comment
Both default off. Enable deliberately:
```bash
export TFACTORY_TRIAGER_GIT_WRITE=1     # commit accepted tests to the feature branch
export TFACTORY_TRIAGER_PR_COMMENT=1    # gh pr comment with the report
```
With these off (the default), the Triager renders the report and the `pr_comment_body.md` but writes nothing to git or the PR.

### Emit / consume the completion-event envelope
```bash
export TFACTORY_COMPLETION_WEBHOOK=https://watcher/tfactory   # POST the RFC-0001 envelope
export TFACTORY_COMPLETION_SENTINEL=1                          # also write findings/COMPLETED.json
```
A watcher stats `COMPLETED.json` or receives the POST — no polling. `correlation_id` is the GitHub issue #.

### Prepare and send a handback
On a failing run the Triager prepares `findings/handback_request.{md,json}` (default `TFACTORY_HANDBACK_PREPARE` ON). Send it:
```bash
python -m agents.handback <spec_dir> --send   # or /handback-to-aifactory
```
AIFactory writes `QA_FIX_REQUEST.md` on the original spec and runs the QA Fixer.

### Drive the bounded fix loop
`/tfactory-fixloop <task_id>` runs one cycle: handback → AIFactory fix → re-test. Repeat (drive with `/loop`) until the run passes, the same tests keep failing (no progress), or `TFACTORY_HANDBACK_MAX_CYCLES` (default 2) is hit → `stuck`.

### Decide flag vs reject vs hand back
- **flag** — keep the test, surface a caveat (signals disagree, low confidence) for a human; no code change implied.
- **reject** — drop the test entirely (vacuous/harmful); it never reaches the report.
- **hand back** — the *code under test* is wrong (tests aligned to the AC fail). Package a correction and send it to AIFactory's QA Fixer.

### Read a `triaged_empty` result correctly
An empty report is not a failure. It means either every candidate was a reject (all tests vacuous/harmful) or duplicates collapsed to nothing. Distinguish it from `triager_failed` (the Triager errored) by the status: `triaged_empty` is a clean terminal state and still emits a completion event. Don't trigger a handback off an empty report unless there were genuine failing-but-correct tests.

### Wire up a polling-free watcher
Instead of polling, point a watcher at the sentinel or webhook so it learns of completion the moment the Triager finishes:
```bash
export TFACTORY_COMPLETION_SENTINEL=1                 # findings/COMPLETED.json appears on terminal status
# watcher: stat findings/COMPLETED.json, then read findings/triage_report.json
```
`/tfactory-watch` consumes this so a handover round-trip needs no polling loop.

### Confirm what actually shipped
After a run, don't assume side-effects happened — confirm each:
- Git commit: only if `TFACTORY_TRIAGER_GIT_WRITE=1` was set; otherwise the commit was logged dry-run.
- PR comment: only if `TFACTORY_TRIAGER_PR_COMMENT=1` *and* `source.json` has a PR#; otherwise look for `findings/pr_comment_body.md`.
- Handback sent: only if `TFACTORY_HANDBACK_SEND=1`; otherwise the artifact was prepared but not transmitted.
Each defaults OFF under the no-automatic-pushes policy, so the safe assumption is "nothing was pushed unless I opted in".

### Trace one finding through the Triager
Follow a single accepted test from verdict to report to understand the deterministic chain:
1. Evaluator wrote it to `verdicts.json` as `accept`, confidence 0.88, mutation KILLED.
2. Triager loads it as a TriageCandidate (not a reject, so it survives).
3. Dedup: no byte-identical twin → it stays.
4. Rank: high verdict_priority + KILLED mutation + 3/3 stability + positive coverage_delta → near the top of the ordering.
5. Render: it appears in `triage_report.{md,json}` with its signals summarized.
6. Side-effect: committed *only if* `TFACTORY_TRIAGER_GIT_WRITE=1` and its `commit_readiness` was true.
Same inputs always produce this same path — the Triager has no randomness or LLM.

### Read the completion envelope downstream
A watcher receiving the RFC-0001 envelope can branch on `outcome` without re-reading the workspace:
```jsonc
{
  "schema_version": "1", "event": "task.completed", "service": "tfactory",
  "correlation_id": "224",        // GitHub issue # — the correlation_key
  "outcome": "triaged",           // triaged | triaged_empty | triager_failed
  "status": "triaged"             // legacy flat field, kept for compatibility
}
```
On `triaged` with failing tests present, that's the trigger to consider a handback; on `triaged_empty`, there's nothing to do.

---

## Gotchas
1. **Assuming tests were committed** — by default nothing is pushed. If you didn't set `TFACTORY_TRIAGER_GIT_WRITE=1`, the commit was a dry-run log only. Check the flag before claiming "tests committed".
2. **Confusing reject with handback** — reject is "this *test* is bad, drop it"; handback is "the *code* is bad, fix it". They go opposite directions; don't reject a good failing test that's actually catching a real bug — hand it back.
3. **Unbounded fix loops** — without respecting `TFACTORY_HANDBACK_MAX_CYCLES`, a stuck correction cycle thrashes forever. The cap exists; honor it and route to `stuck`.
4. **Mismatched correlation_key** — if the handback or re-test keys off a different id than the completion envelope, artifacts won't reconcile and the loop loses the thread. Keep one correlation_key per task.
5. **Best-effort callback treated as required** — a failing completion webhook is logged, not fatal; don't block the pipeline on it. Conversely, don't assume delivery succeeded without checking the sentinel.
6. **Empty report ≠ failure** — `triaged_empty` means everything was deduped/rejected away or there were no candidates; it's a valid terminal status, not an error.
7. **Posting a PR comment with no PR** — when `source.json` has no PR#, there's nothing to comment on; the Triager writes `pr_comment_body.md` instead. Don't expect a posted comment.
8. **Re-running a handback without resetting state** — re-sending the same correction without re-testing first can re-trigger a cycle that's already been counted. Let `/tfactory-fixloop` own the cycle counter so `MAX_CYCLES` stays accurate.
9. **Forgetting handback is two steps** — preparing the artifact (default ON) and sending it (default OFF) are separate. A prepared-but-not-sent handback looks "ready" but AIFactory never heard about it. Confirm `TFACTORY_HANDBACK_SEND=1` or run `--send` explicitly.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Assuming the Triager pushed tests | Side-effects are dry-run by default | Set `TFACTORY_TRIAGER_GIT_WRITE=1` to actually commit |
| Rejecting a failing test that caught a real bug | Reject drops signal; the bug is in the code | Hand it back to AIFactory's QA Fixer |
| Ranking before deduping | Duplicates inflate and distort the order | Dedup (`triage_dedup.py`) first, then rank |
| Running the fix loop unbounded | Stuck corrections thrash forever | Honor `TFACTORY_HANDBACK_MAX_CYCLES`; route to `stuck` |
| Different ids across handback/envelope/re-test | Artifacts can't reconcile to the task | Use one correlation_key end-to-end |
| Treating a failed completion webhook as fatal | Callbacks are best-effort by design | Log and continue; verify via sentinel if needed |
| Reading `triaged_empty` as a failure | Empty is a valid terminal outcome | Distinguish empty (no candidates) from `triager_failed` |
| Expecting a PR comment with no PR# | There's no PR to comment on | Read the rendered `pr_comment_body.md` instead |
| Preparing a handback and assuming it sent | Prepare (ON) and send (OFF) are separate steps | Set `TFACTORY_HANDBACK_SEND=1` or run `--send` |
| Expecting the Triager to make quality calls | It has no LLM; judgment is the Evaluator's job | Look to verdicts.json for quality; the Triager only orchestrates |
| Re-sending a correction without re-testing | Double-counts or skips the cycle counter | Drive cycles through `/tfactory-fixloop` so MAX_CYCLES is honored |

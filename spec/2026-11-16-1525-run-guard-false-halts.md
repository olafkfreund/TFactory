---
status: draft
issue: 1525
intent: intent/2026-11-16-1525-run-guard-false-halts.md
---

# Spec: Run guard false halts

## Design

Applies to `apps/backend/agents/run_guard.py` in TFactory and PFactory (same `Controller`).

**1. Same-tool halt by signature.** Drop the per-tool-name `_tool_fail` counter. The halt keys on the existing tool+input signature streak (`_Sig.fail_streak`, reset by a success of that same call):

- `before_call`: when the signature already failed `repeat_fail` (5) times, the call is blocked. Each blocked attempt also counts as a failure of that signature; at `tool_fail_halt` (8) the session halts with `same_tool_failure: <tool> failed Nx with identical input`.
- `after_call`: no name counter; success clears only that signature's streak.

A blocked call never reaches the tool, so without counting blocked attempts the halt would be unreachable. Result: eight failing Bash calls with different commands never halt; a model that keeps retrying one identical failing call is blocked at 5 and halted at 8.

**2. TFactory token ceiling per run.** `ceiling_exceeded` compares `total - baseline`, where the baseline is the persisted total seen on the first call for that spec dir in this process (a verify Job is one process). Persisted usage stays cumulative for reporting. A new Job for a task that crossed the ceiling earlier starts from a fresh baseline.

**3. PFactory token ceiling per run.** `PlanSession` gets a private `_run_base_tokens`, set in `process()` to `usage.total_tokens` at entry. `record_usage` and `_decompose` check `usage.total_tokens - _run_base_tokens`. `session.usage` stays cumulative.

## Alternatives rejected

- Raise `tool_fail_halt` or reset on any success: still halts long failing iteration.
- Remove the same-tool halt: loses the true-loop stop.
- Reset persisted usage on re-run: loses cumulative cost reporting for CFactory.
- AIFactory's per-turn controller: the ports hold one controller per client with no turn boundary.

## Risks

- TFactory baseline is per process: a long-lived process re-verifying one spec dir keeps its first baseline. Verify runs as a Job, so accepted (code comment marks the limit).
- A loop alternating two failing calls never reaches 8 on one signature; it is bounded by `max_turns` and the ceiling.

## Verification

Regression tests in each repo fail before and pass after: distinct failing Bash commands (>8) do not halt; an identical failing call halts at 8; a spec dir already over the ceiling in a prior run passes in a new process, and a run crossing it still halts (PFactory: re-`process()` after an over-ceiling run). Existing tests updated where they asserted the by-name halt.

---
status: approved
issue: 1342
spec: spec/2026-10-21-1342-liveness-sweep-deadline.md
---

# Plan: name the deadline and max idle in the liveness sweep summary

## Approved decisions (self-contained)

- Summary becomes `liveness sweep: %d task(s) checked, %d stalled (deadline %.0fs, max idle %.0fs)`.
- Deadline = `deadline_seconds` or `agents.liveness._deadline_seconds()`.
- Max idle = max non-None `verdict.idle_seconds`, default 0.
- Predicate unchanged; item 3 of the issue is out of scope.

## Steps

1. `apps/web-server/server/background/liveness_sweep.py` `run_one_sweep`:
   compute deadline and max idle, extend the summary log line.
2. `tests/test_web_liveness_sweep.py`: add a caplog test asserting the
   deadline and max idle are in the summary.
   → `pytest tests/test_web_liveness_sweep.py`, `ruff check`.

## Rollback

Revert the commit; log text only.

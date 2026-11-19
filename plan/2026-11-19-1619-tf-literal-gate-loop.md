---
status: approved
issue: 1619
spec: spec/2026-11-19-1619-tf-literal-gate-loop.md
coders:
  1: lead   # small contained edit, kept by the lead
reviewers:
  - codex
---

# Plan: waive the literal gate after repeated rejections

Decisions from the spec: waive after 2 prior same-lineage literal rejections, record a warning, never waive other guardrails; partial-verify unchanged (evidence: Job dispatched and triaged).

## Steps

1. apps/backend/agents/gen_functional.py: add `_waive_repeated_literal_rejection` and call it at the guardrail site in `_generate_one_subtask` → verify by tests/test_literal_gate_waiver.py
   Traps: lineage regex must strip chained `-rN` suffixes.
2. apps/backend/agents/gen_functional.py: waiver skips only the literal check (`waive_literal`), so the authority guard still runs; `_record_unresolvable_imports` keeps existing warnings so the waiver record survives (Codex review findings P1, P2).
3. apps/backend/agents/ac_fidelity.py: `_find_verdict` strips chained `-rN` suffixes so a waived/accepted `x-r1-r1` test counts for its AC (Codex finding P2).
4. tests/test_literal_gate_waiver.py: new tests with the Go and notes-api criterion texts → verify by pytest.

## Tests

`.venv/bin/pytest tests/test_literal_gate_waiver.py tests/test_gen_functional*.py tests/test_criterion_literals.py` green; ruff check + format; scripts/ratchet_lint.py for apps/backend and apps/web-server.

## Rollback

Revert the commit; the gate returns to rejecting every time.

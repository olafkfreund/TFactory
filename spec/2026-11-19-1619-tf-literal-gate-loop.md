---
status: approved
issue: 1619
intent: intent/2026-11-19-1619-tf-literal-gate-loop.md
---

# Spec: waive the literal gate after repeated rejections

## Design

In `gen_functional._generate_one_subtask`, after `_source_guardrail_rejection` returns a `gen_functional_criterion_literal_rejected` rejection, `_waive_repeated_literal_rejection` counts prior literal rejections of the same lineage in `status.json:replan_reasons` (subtask id with trailing `-rN` removed). With 2 or more prior, the rejection is dropped, the test is committed, and a `gen_functional_warnings` entry records the waiver. Lineage, not identical reason text, is the key because the reason changes every round ("8", "0, 8", "61, 0, 8"). The verdict and AC-fidelity ledger still grade the test.

## Alternatives rejected

- Teach the gate derived values (len of padded strings, constant folding of `MAX + 1`, per-case criteria splitting): open-ended, language-specific, still loops on the next shape.
- Raise or remove the global budget: hides the cause and costs more.
- Change partial-verify: evidence shows it already dispatches the Job and triages; nothing to fix.

## Risks

A genuinely reinterpreted criterion on the third attempt is kept; it is flagged by the warning, and the AC-fidelity ledger / verdict still apply.

## Verification

tests/test_literal_gate_waiver.py with the two real criterion texts (gate still flags them; waiver after 2 lineage rejections; none for first rejections, other lineages, other guardrails).

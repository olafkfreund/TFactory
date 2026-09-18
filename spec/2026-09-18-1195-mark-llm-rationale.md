---
status: approved
issue: 1195
intent: intent/2026-09-18-1195-mark-llm-rationale.md
---

# Spec: Mark LLM-authored rationale in the handback artifacts

Decisions carried from intent review: also mark the triage report and PR
comment (same text); track "jest lane on a repo with no committed flake" as a
post-deploy check on #1195, not code.

## Findings (dev @ 1a5c1c5c+)

`verdict["reasons"]` is **not** purely model text. The judge writes it, then
deterministic code edits it:

| Writer | Effect | Where |
| --- | --- | --- |
| flaky-history demotion | appends | `confidence.py:205` |
| consistent-fail classifier (#629/#892) | **replaces** the list | `confidence.py:273` |
| app-not-healthy → `not_run` | appends | `confidence.py:302` |
| majority-vote override (#649) | appends | `evaluator.py:2674` |
| unjudged test entry (#1274) | writes the whole list | `evaluator.py:_unjudged_entry` |

So labelling all of `reasons` "LLM-authored" would mislabel measured facts —
the same error in the other direction. Provenance has to be recorded where
each line is written.

Renderers of that text: the handback (`handback/request.py:_join_reasons` →
`render.py:46`, printed as **"Observed:"**) and the triage report
(`triage_report.py:446-449`, `- reason: …`), whose Markdown is also the PR
comment body (`triager.py:1457,1487`).

## Design

1. **Provenance, recorded at write time.** A parallel list
   `verdict["reasons_source"]`, index-aligned with `reasons`, values
   `"model"` | `"system"`.
   - New helper in `confidence.py`: `add_system_reason(verdict, text)` —
     back-fills `reasons_source` with `"model"` for any existing reasons it
     does not yet cover, then appends `text` / `"system"`. And
     `set_system_reasons(verdict, texts)` for the replace case.
   - All five writers above use the helpers.
   - **No stamping of the judge's output is needed:** any reason without a
     `"system"` tag is model-authored by construction, because every
     deterministic writer goes through the helper. A verdict with no
     `reasons_source` (older runs) is read as all-`"model"` — the safe
     direction (a fact shown as opinion misleads less than an opinion shown as
     fact).
   - One reader helper, `reason_lines(verdict) -> list[tuple[str, str]]`
     (text, source), used by every renderer, so the default-to-model rule lives
     in one place.
2. **Handback JSON (additive, cross-repo contract).** Each failure keeps
   `reason` (unchanged string, so AIFactory's current receiver keeps working)
   and gains `reasons: [{"text": …, "source": "model"|"system"}]`. The
   request gains `"reason_provenance": "reasons[].source; model = written by
   the Evaluator's judge LLM"` once at the top level, so the field explains
   itself.
3. **Handback Markdown (`render.py`).** Replace the single `- **Observed:**`
   line with, per failure:
   - `- **Measured:** <system lines>` (omitted when none)
   - `- **Evaluator model's rationale (LLM-authored, not verified):** <model
     lines>` (omitted when none)
   Plus one line in the header blockquote: "Lines marked LLM-authored are the
   Evaluator model's interpretation, not a measurement — verify before acting
   on them." Output stays deterministic (snapshot-testable).
4. **Triage report / PR comment (`triage_report.py`).** `- reason: …` becomes
   `- measured: …` or `- model (LLM-authored): …` per line.

## Alternatives rejected

- **Label the whole `reasons` field "LLM-authored"** — mislabels the five
  deterministic writers' facts; the classifier's text exists precisely because
  the model's reason was wrong (#629).
- **Separate `system_reasons` field instead of `reasons`** — changes what every
  existing reader of `reasons` sees (triage, val_block, confidence, the
  receiver); the parallel-list approach leaves `reasons` byte-identical.
- **Heuristic detection (regex on known deterministic phrases)** — silently
  wrong the day someone rewords a message.
- **Change AIFactory's receiver in this task** — other repo; the additive
  field lets it adopt `reasons[].source` in its own time.

## Risks

- A future deterministic writer that appends to `reasons` directly (bypassing
  the helper) would be labelled `model`. Mitigation: a test that greps
  `apps/backend/agents` for direct `reasons` mutation outside the helpers and
  fails, so the rule is enforced rather than remembered.
- Snapshot tests of `render.py` / triage report change — expected; updated in
  the same PR.
- Cross-repo: none breaking (additive only). AIFactory still renders the old
  `reason` string until it opts in.

## Verification

- Unit: each of the five writers yields `reasons_source` aligned with
  `reasons` and tagged `system` on its own line; judge lines untouched read
  `model`; a verdict without `reasons_source` reads all-`model`.
- Replace case: consistent-fail classifier → `["system"]` only.
- Render: handback Markdown has no "Observed:" line; a mixed verdict shows
  both a Measured and an LLM-authored line; JSON keeps `reason` unchanged and
  adds `reasons[]`.
- Enforcement test: a direct `verdict["reasons"].append(` outside the helpers
  fails it (mutation-checked by adding one).
- Suites + ratchet (pinned ruff/mypy) green.
- Post-deploy (tracked on #1195): one handback prepared on the cluster shows
  the split; plus the separate "jest lane on a repo with no committed flake"
  run.

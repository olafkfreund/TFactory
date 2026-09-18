---
status: approved
issue: 1195
spec: spec/2026-09-18-1195-mark-llm-rationale.md
---

# Plan: Mark LLM-authored rationale in the handback artifacts

Approved decisions (self-contained):

- Provenance is recorded **at write time** in `verdict["reasons_source"]`, a
  list index-aligned with `reasons`, values `"model"` | `"system"`.
  `reasons` itself stays byte-identical for every existing reader.
- Helpers in `agents/confidence.py`:
  - `add_system_reason(verdict, text)` — back-fill `reasons_source` with
    `"model"` for uncovered existing reasons, then append `text`/`"system"`.
  - `set_system_reasons(verdict, texts)` — replace both lists (all `"system"`).
  - `reason_lines(verdict) -> list[tuple[str, str]]` — the one reader; a
    missing/short `reasons_source` reads as `"model"` (safe direction).
- The five deterministic writers use the helpers: flaky demotion
  (`confidence.py:205`), consistent-fail classifier (`:273`, replace),
  app-not-healthy (`:302`), majority-vote override (`evaluator.py:2674`),
  unjudged entry (`evaluator.py:_unjudged_entry`, `:2601`). The judge's own
  output is not stamped — untagged = model by construction.
- Handback JSON is **additive** (cross-repo contract with AIFactory): each
  failure keeps `reason` unchanged and gains
  `reasons: [{"text", "source"}]`; the request gains a top-level
  `reason_provenance` explainer.
- Handback Markdown (`handback/render.py`): the `- **Observed:**` line is
  replaced by `- **Measured:** …` (system lines, omitted if none) and
  `- **Evaluator model's rationale (LLM-authored, not verified):** …` (model
  lines, omitted if none), plus one header note. Deterministic output.
- Triage report (`triage_report.py:446-449`, which is also the PR comment
  body): `- reason: …` → `- measured: …` / `- model (LLM-authored): …`.
- An enforcement test fails on any direct mutation of `reasons` in
  `apps/backend/agents` outside the helpers.
- Post-deploy checks tracked on #1195 (not code): one real handback shows the
  split; the "jest lane on a repo with no committed flake" run.
- Lands **before #1174**, which reuses `add_system_reason`.

Branch: `fix/1195-mark-llm-rationale` (off `origin/dev`).

## Steps

1. **Tests first (must fail on dev).** New `tests/test_reason_provenance.py`:
   - helper unit tests: back-fill + append; replace; `reason_lines` default
     to model for no/short `reasons_source`;
   - each of the five writers tags its own line `system` and leaves judge
     lines `model` (flaky, consistent-fail replace → `["system"]` only,
     app-not-healthy, majority vote, unjudged entry);
   - enforcement: AST scan of `apps/backend/agents/**/*.py` finds no
     `X["reasons"].append(`, `X["reasons"] = `, `reasons.append(` on a
     verdict's list, or a dict literal with a `"reasons"` key, outside
     `confidence.py`'s helpers and an explicit allow-list (the judge-parse
     path, if any, and the unjudged entry once converted);
   - handback: `render_fix_request_md` has no `Observed:`; a mixed verdict
     renders both a Measured and an LLM-authored line; JSON keeps `reason`
     unchanged and adds `reasons[]` + `reason_provenance`.
   → verify by import/assert failures on unmodified dev.
2. `confidence.py`: add the three helpers.
   → verify by the helper unit tests passing.
3. `confidence.py` writers (`:205`, `:273`, `:302`) and `evaluator.py`
   (`:2674` vote override, `_unjudged_entry`) switch to the helpers.
   → verify by the five writer tests passing and `tests/test_confidence.py`,
   `tests/test_confidence_corpus.py`, `tests/test_evaluator.py` unchanged and
   green (`reasons` text byte-identical).
4. `handback/request.py`: `Failure` gains `reasons: tuple[tuple[str,str],...]`
   (from `reason_lines`), `to_dict` adds `reasons[]`; `CorrectionRequest.to_dict`
   adds `reason_provenance`. `reason` stays `_join_reasons(entry)` unchanged.
   → verify by the JSON test; `tests/test_handback_request.py`,
   `test_handback_send.py`, `test_handback_283.py`, `test_correlation_sync.py`
   green.
5. `handback/render.py`: Measured / LLM-authored lines + header note.
   → verify by the render test.
6. `triage_report.py` `_candidate_md_block`: per-line label via
   `reason_lines`; update `tests/fixtures/triage_report/expected.md` in the
   same commit (the only expected snapshot change).
   → verify by the triage snapshot test passing with the reviewed diff to
   `expected.md` limited to `reason` lines.
7. Mutation checks: (a) make one writer append directly → enforcement test
   fails; (b) drop the default-to-model rule in `reason_lines` → the legacy
   verdict test fails.
   → verify by observed failure, then restore.
8. Full suites + ratchet (pinned: `scripts/ratchet_lint.py` with ruff 0.15.17
   / mypy 1.20.1, run **after** committing), format check.
   → verify all green.
9. Commit, push, PR to `dev` linking intent/spec/plan.
   → verify CI green; merge per the user's instruction.
10. Post-deploy: record on #1195 the real handback check and the
    no-committed-flake jest run; close #1195 when both are observed.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_reason_provenance.py tests/test_confidence.py tests/test_confidence_corpus.py tests/test_evaluator.py tests/test_handback_request.py tests/test_handback_send.py tests/test_handback_283.py tests/test_correlation_sync.py -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest tests/ -m "not slow" -q
TMPDIR=/var/tmp apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q
PATH=<ratchet-venv>/bin:$PATH python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the commit. `reasons` is untouched, `reasons_source` and the JSON
`reasons[]` are additive, so older readers and AIFactory's receiver are
unaffected either way; the only visible change reverted is the Markdown labels.

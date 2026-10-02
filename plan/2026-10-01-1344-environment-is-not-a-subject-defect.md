---
status: approved
issue: 1344
spec: spec/2026-10-01-1344-environment-is-not-a-subject-defect.md
---

# Plan: a failure that never reached the subject is `not_run`

## Approved decisions (self-contained)

- **Why.** Run `026-myfriends-remediation-verify` reported 21 failing tests, all
  21 environmental, none reaching the subject. 13 were classified `assertion`,
  so the hand-back told AIFactory "the test executed and its assertions failed
  (subject behaviour is wrong)" for code never contacted. The hand-back says
  "fix the code under test — do not weaken or delete the tests", so complying
  means breaking correct code or weakening tests. That is the harm being fixed.
- **Reuse `not_run`, do not invent a verdict.**
  `apply_app_not_healthy_override` already sets `verdict["verdict"] = "not_run"`
  for an infra failure, and `handback/request.py:41` is
  `_FAILING_VERDICTS = frozenset({"reject"})`. So `not_run` is **already**
  excluded from hand-backs; the harm prevention costs no hand-back change.
- **Detect by literal marker, never by inference.** The markers are
  `KeyError: 'TFACTORY_TARGET_URL'` and its double-quoted form. A regex over
  prose would repeat the defect this issue is about.
- **Ordering is the fix for this case.** Such a run exits 1 with `FAILED` in its
  output, so it matches the `assertion` branch today. The new branch goes after
  `app_not_healthy` and before `import` and `assertion`.
- **Not covered: the `/tmp/.worktree` shape.** It raises a genuine
  `AssertionError` on author-chosen text; no marker separates it from a real
  failure without guessing. 2 of the 13 stay misreported until #1347 lands. Said
  plainly rather than implied away.
- **`import` stays as it is.** Unlike a missing target URL, an import error may
  be the *subject's* broken import — a real defect it owns. Suppressing
  hand-backs for `import` would hide that.
- **Scope is the misreporting only.** The api lane's missing target is #1347.
- **An unreached test suppresses only its own item**, which falls out of
  `build_correction_request` filtering per entry.

## Steps

Branch `fix/1344-environment-is-not-a-subject-defect` off `dev` (already
created). One commit per step.

1. **Fixture first.** Commit the real `stdout_tail` for
   `delete-account-wrong-owner` from run 026's `lane_runs.json` as
   `tests/fixtures/lane_runs/target_url_keyerror.txt`, plus the
   `store-no-inmemory-dict` tail as `worktree_absent.txt` for the
   not-covered case.
   → verify: a test asserts both files are non-empty and that the first
   contains `KeyError: 'TFACTORY_TARGET_URL'`. A fixture that silently fails to
   load would make every later case vacuous.
2. **`classify_pytest_failure` gains `environment`** in
   `apps/backend/agents/stability_runner.py`: `_ENVIRONMENT_MARKERS` plus a
   branch placed after `app_not_healthy`, before `import` and `assertion`.
   → verify: the step-1 fixture classifies as `environment`; it classifies as
   `assertion` before this step. `worktree_absent.txt` still classifies as
   `assertion` — the documented gap, asserted so it cannot be mistaken for
   coverage later.
   **Mutation:** move the branch below `assertion` — the first test must fail,
   proving the ordering carries the fix.
3. **`apply_environment_override`** in `apps/backend/agents/confidence.py`,
   a sibling of `apply_app_not_healthy_override`: same signature, fires only on
   `failure_kind == "environment"`, sets `not_run`, adds a system reason naming
   the missing target URL. Never touches an `accept`.
   → verify: an `environment` verdict becomes `not_run` with that reason; an
   `assertion` verdict is untouched; an `accept` is untouched.
   **Mutation:** remove the `failure_kind` guard — the assertion-verdict case
   must fail.
4. **The harm, asserted directly.** A test over `build_correction_request` with
   one `environment` failure and one genuine `reject`: the hand-back carries
   **only** the `reject`.
   → verify: this is the test that states the property the issue exists for,
   rather than the three mechanisms that happen to produce it. It must fail
   before step 3 and pass after.
5. **Reason text.** A test that `apply_consistent_fail_reason` never writes
   "subject behaviour is wrong" for an `environment` kind. It already returns
   `False` for unknown kinds, so this pins incidental-today safety.
   **Mutation:** add an `else` that falls through to the assertion wording —
   the test must fail.
6. **Assurance level.** A run whose only failures are `environment` must not
   have its level raised — the never-overclaim gate still sees `not_run`.
7. **Gates:** `ruff check`, `ruff format --check` over the CI path list,
   `scripts/ratchet_lint.py --base origin/dev` with its `--package` flags, the
   hub security-sinks lint, the full suite, and **each new test module collected
   alone** (a module that only passes alongside others is one shuffle from
   vanishing).
8. **PR → `dev`** linking intent, spec and plan; close #1344. File the
   unit-sandbox dependency gap (the 8 `ModuleNotFoundError` failures) as its own
   issue, since this plan deliberately leaves it.

## Deviations recorded during implementation

- **Step 3 was incomplete: it named the function and not its wiring.** As
  written, step 3 produced `apply_environment_override` and three tests of it
  in isolation — and nothing that calls it. `apply_app_not_healthy_override`,
  the sibling it mirrors, is invoked from `enrich_verdicts`
  (`agents/confidence.py:461`); without the same call the new override never
  runs in production, and all three of its tests still pass. That is "guard
  written but not wired" — the identical defect shape as AIFactory#1638, which
  this session is fixing in parallel.

  Step 3 therefore also adds the call, immediately after
  `apply_app_not_healthy_override` so the ordering note there still holds, and
  **a test through `enrich_verdicts`** rather than against the function alone.
  The wired test is the one that matters: a standalone test passes whether or
  not anything calls it, which is exactly how this would have shipped.

  Caught by the coder, which is the point of asking before improvising.

## Tests

```sh
V=apps/backend/.venv/bin
$V/python -m pytest tests/ -q -k "classify or stability_runner"
$V/python -m pytest tests/ -q -k "confidence or override"
$V/python -m pytest tests/ -q -k "handback or correction_request"
$V/python -m pytest tests/ -q                      # full
$V/python -m pytest tests/test_stability_runner.py -q   # collected alone
$V/python -m pytest tests/test_confidence.py -q         # collected alone
$V/ruff check apps/backend apps/web-server tests scripts
$V/ruff format --check apps/backend apps/web-server tests scripts
$V/python scripts/ratchet_lint.py --base origin/dev \
  --package apps/backend --package apps/web-server --package scripts
```

Expected: each new test fails before its step and passes after; all four
mutations fail; the `worktree_absent` case still reads `assertion`.

## Rollback

Revert the PR. These failures classify as `assertion` again and re-enter
hand-backs — no worse than today. Nothing persists: classification is computed
per run and no stored verdict is rewritten by this change.

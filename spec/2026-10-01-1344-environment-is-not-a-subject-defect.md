---
status: draft
issue: 1344
intent: intent/2026-10-01-1344-environment-is-not-a-subject-defect.md
---

# Spec: a failure that never reached the subject is `not_run`

## Open questions, as resolved

The intent left three for the approver. Approval was given without separate
answers, so these are resolved as the recommendations stated there. Each is
flagged so it can be overturned at spec review rather than discovered later.

1. **Scope is the misreporting only.** The api lane's missing target is now
   understood precisely — the target snapshot its resolver reads is written only
   by the `task_control` path (#1347) — and is not fixed here. A correct
   "not run" is useful while that remains, and conflating the two would make
   this change untestable without also fixing #1347.
2. **The 8 `ModuleNotFoundError` failures stay out of scope**, and stay
   classified as `import`. Unlike a missing target URL, an import error is
   genuinely ambiguous: the sandbox may lack a dependency, *or* the subject may
   have a broken import — which is a real defect the subject owns. Suppressing
   hand-backs for `import` would hide that. The sandbox not installing the
   subject's dependencies is a separate gap, worth its own issue.
3. **An unreached test suppresses only its own hand-back item.** This falls out
   of the design rather than needing a decision: `build_correction_request`
   filters per verdict entry, so a lane that measured real failures still hands
   those back.

## Design

The pattern already exists. `apply_app_not_healthy_override`
(`agents/confidence.py:~317`) reclassifies an api-lane failure whose app never
came up:

```python
verdict["verdict"] = "not_run"
add_system_reason(verdict, "app-under-test never became healthy ... (infra not_run, ...)")
```

and `agents/handback/request.py:41` is:

```python
_FAILING_VERDICTS = frozenset({"reject"})
```

So `not_run` is **already** excluded from hand-backs. Reusing that verdict buys
the harm prevention without touching the hand-back code at all. Three changes:

### 1. `classify_pytest_failure` gains an `environment` kind

`agents/stability_runner.py:114`. A new branch, placed after
`app_not_healthy` and **before** `import` and `assertion`:

```python
if _TARGET_URL_UNSET_MARKERS & ... :   # see below
    return "environment"
```

Detection is by marker, never by inference — the intent's first constraint. The
markers are the literal text pytest prints when the lane's target is absent,
both quoting styles:

```python
_ENVIRONMENT_MARKERS = (
    "KeyError: 'TFACTORY_TARGET_URL'",
    'KeyError: "TFACTORY_TARGET_URL"',
)
```

Ordering matters and is deliberate: such a run exits 1 with `FAILED` in its
output, so it matches the `assertion` branch today. Placing it earlier is the
whole fix for this case.

**Not covered: the `/tmp/.worktree` shape.** `assert _STORE_PATH.exists(),
"store.py not found at ..."` raises a genuine `AssertionError` on text the test
author chose. No marker distinguishes it from a real assertion failure without
guessing at prose, which the intent forbids. That case is prevented upstream —
by a populated checkout (#1347, #376, #729) — not classified here. Two of the
thirteen misreported failures therefore remain misreported until #1347 lands,
and this spec says so rather than implying full coverage.

### 2. `apply_environment_override` in `agents/confidence.py`

A sibling of `apply_app_not_healthy_override`, same signature and contract:
takes `(verdict, failure_info)`, returns `True` when it changed the label, sets
`verdict["verdict"] = "not_run"`, and adds a system reason naming the cause —
"the lane had no target URL, so the test never contacted the subject". Fires
only on `failure_kind == "environment"`. Never touches a genuine `accept`.

### 3. `apply_consistent_fail_reason` must not claim a subject defect

Same module, `~line 225`. Its `kind == "assertion"` branch writes "the test
executed and its assertions failed (subject behaviour is wrong)". It currently
has `if/elif/else: return False`, so an unknown kind already falls through
safely — `environment` will not be described as an assertion failure by
accident. A test pins that, because the safety is incidental today and a future
`else` clause could remove it.

## Alternatives rejected

- **A new verdict category (`environment`) alongside `not_run`.** More faithful
  in name, but it would need teaching to the hand-back filter, the
  never-overclaim gate and the assurance-level computation, each a place to get
  it wrong. `not_run` already means "this measured nothing" to all three.
- **Suppressing the whole hand-back when any lane was unreached.** Rejected per
  open question 3: it would discard real findings from lanes that did run.
- **Detecting "the subject was never reached" generically**, e.g. by asserting
  the lane issued at least one request. Stronger in principle, and the right
  long-term answer, but it needs a request-level signal the lane does not emit
  today. Out of scope.
- **Fixing it in the classifier's exception-type check.** Impossible for the
  worktree case, which raises a real `AssertionError`; recorded above.

## Risks

- **Over-suppression.** If the marker ever appeared in a genuine subject
  failure's output, that failure would be silently downgraded to `not_run` and
  dropped from the hand-back. The marker is a `KeyError` on a variable only the
  lane sets, so a subject cannot plausibly raise it — but this is the one way
  this change could hide a real defect, and it is why detection is a literal
  marker rather than a pattern.
- **A green-looking run that measured nothing.** `not_run` is honest, but a
  reader skimming for failures will see fewer. The never-overclaim gate already
  downgrades the assurance level on `not_run`, which is the existing mitigation;
  verification below checks it still applies.
- **No host risk.** Classification and labelling only; nothing changes about
  what is executed.

## Verification

Each step's test must fail before it and pass after, and each fix is mutated
independently.

1. **The classifier**, against **real captured output** rather than
   hand-written strings: the `stdout_tail` from run
   `026-myfriends-remediation-verify`'s `lane_runs.json` for
   `delete-account-wrong-owner`, committed as a fixture. It currently
   classifies as `assertion`; it must classify as `environment`.
   **Mutation:** move the new branch below the `assertion` branch — the test
   must fail, proving the ordering is what does the work.
2. **The override** turns such a verdict into `not_run` with a reason naming the
   missing target. **Mutation:** remove the `failure_kind` guard so it fires on
   anything — a genuine assertion-failure verdict must then fail its test.
3. **The harm, asserted directly.** `build_correction_request` over a verdict
   set containing one `environment` failure and one genuine `reject` must
   produce a hand-back carrying **only** the `reject`. This is the test that
   matters: it states the property the issue exists for, rather than testing the
   three mechanisms that happen to produce it.
4. **The reason text** for an `environment` kind never contains "subject
   behaviour is wrong".
5. **The assurance level** for a run whose only failures are `environment` is
   not raised — the never-overclaim gate still sees `not_run`.
6. **Count check.** A test asserts the fixture set is non-empty, so a broken
   fixture load cannot make every parametrised case vanish and still report
   success.
7. **Gates:** ruff, `ruff format --check` on the CI path list,
   `ratchet_lint.py --base origin/dev` with its `--package` flags, the full
   suite, and each new test module collected alone.

Not verified by a live run, deliberately: reproducing it end to end requires an
api lane with no target, which is #1347's subject. The fixture is that run's
real output, which is the same evidence without the dependency.

## Rollback

Revert the PR. The classifier returns `assertion` for these again and the
hand-back re-includes them — no worse than today, and no stored state to unwind:
classification is computed per run and nothing persists it.

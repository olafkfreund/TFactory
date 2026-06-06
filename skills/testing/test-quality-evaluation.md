# test-quality-evaluation

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: evaluation,test-quality,mutation-testing,coverage,flaky-tests,verdict,semantic-relevance,confidence

---

# Test Quality Evaluation

Use this skill when you need to judge whether a generated or existing test is *good* — when reading or producing a TFactory Evaluator verdict, interpreting the 5-signal pipeline (coverage delta, 3× stability, mutate-and-check, flake-lint promotion, LLM semantic relevance), deciding accept/reject/flag, raising a test's numeric confidence, reasoning about flaky-history flip-rates, or explaining why test evaluation must be structurally separate from test generation (non-self-validation).

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Quality Evaluation

TFactory's Evaluator is the gate between a test being *written* and a test being *trusted*. It never asks the generator "is your test good?" — that would be self-validation. Instead it computes four objective numeric signals in code, adds one LLM-judged semantic signal, and fuses them into a verdict (`accept` / `reject` / `flag`) plus a numeric confidence (0–1) and a `commit_readiness` flag. This skill explains how to read those verdicts and how to make a weak test stronger.

---

## When to use this skill
- Reading `findings/verdicts.json` and needing to understand *why* a test was accepted, rejected, or flagged.
- A test passes but you suspect it asserts nothing meaningful (a tautological / vacuous test).
- You want to raise a test's confidence score before it gets committed.
- You're reasoning about whether a one-off green 3× stability run actually means the test is stable, given its flip-rate history.
- You're explaining the architectural reason the Evaluator is a separate agent from Gen-Functional.
- Do NOT trigger for: writing the test in the first place (that's the Gen-Functional flow), ranking/deduping accepted tests for a report (that's the Triager / triage-and-handback skill), or cloud posture verdicts (that's cloud-posture-testing).

---

## Key principles
1. **Non-self-validation** — the agent that wrote the test never judges it. The Evaluator (`agents/evaluator.py`) is structurally separate from Gen-Functional precisely so a confident-but-wrong generator can't rubber-stamp its own output. This is research-mandated, not stylistic.
2. **Four signals are math, one is judgment** — coverage_delta, stability, mutation, and lint_promotion are computed deterministically in code; only semantic_relevance is the LLM's call. Never let the LLM override the numeric signals — it complements them.
3. **A passing test is necessary but not sufficient** — green is the floor, not the verdict. Mutation and semantic relevance exist precisely because a passing test can still verify nothing.
4. **Confidence is a fusion, not a vote** — the 0–1 confidence reflects agreement *across* signals. A test with strong coverage but a SURVIVED mutant and low semantic relevance should land at low confidence even though it's green.
5. **Flaky-history outranks a single run** — 3× stability passing this run does not clear a test with a bad cross-run flip-rate (`flaky_history.py`). History persists in `test_history.json`.
6. **Reject is for harmful, flag is for uncertain** — reject removes a test from the report; flag keeps it but surfaces a caveat for a human. Default to flag when signals merely disagree; reject only when a signal is clearly damning.
7. **commit_readiness gates side-effects** — even an accepted test isn't auto-committed unless `commit_readiness` is true and the Triager's write flags are opted in.

---

## Core concepts
**The Evaluator's place in the pipeline** — it sits fourth, between the Executor and the Triager: `Planner → Gen-Functional → Executor → Evaluator → Triager`. The Executor has already run the tests in a sandbox and produced `coverage.xml` + `junit.xml`; the Evaluator consumes those artifacts plus the test source to produce `findings/verdicts.json`, which the Triager then dedups and ranks. If you're reading a verdict, the raw run already happened — the Evaluator is post-hoc judgment, not execution.

**EvaluatorSignals bundle** — the per-test struct the Evaluator assembles before judging. It carries the four pre-computed numeric signals plus the context the LLM needs for the fifth.

**coverage_delta** (`coverage_delta.py`) — parses Cobertura `coverage.xml` and does set math on covered lines: which *new* lines under test does this test exercise that weren't covered before? A test that adds zero new covered lines is a strong reject/flag signal.

**stability** (`stability_runner.py`) — re-runs the test 3× via the `runner_fn` seam. All-pass = stable; any flip = unstable. This is intra-run; it catches obvious nondeterminism.

**mutation** (`mutate_probe.py`) — AST-mutates ONE assertion in the code under test and re-runs the test. `KILLED` = the test caught the mutation (good — it actually asserts behavior). `SURVIVED` = the mutation slipped past (bad — the test is weak/tautological). Per-language routing via `mutation_dispatch.py` (Python `mutate_probe`, TypeScript `lang_typescript/mutate_probe` Stryker).

**lint_promotion** (`lint_promotion.py`) — promotes a medium-severity flake-lint finding (e.g. `time.sleep`, unfrozen `datetime.now`) from Gen-Functional into an Evaluator-visible signal so flake risk influences the verdict.

**semantic_relevance** (LLM, `evaluator.md`) — does the test actually verify the *claim of the acceptance criterion*, or just exercise code? high / medium / low. A test that calls the function but asserts on an unrelated field is low relevance even if coverage and mutation look fine.

**verdict + confidence + commit_readiness** — `accept|reject|flag`, a 0–1 confidence, and a boolean readiness flag, written to `findings/verdicts.json` and validated (test_id present, verdict in the allowed set).

**The runner_fn seam** — both `stability_runner` (3× re-run) and `mutate_probe` (mutate-then-rerun) execute through a `runner_fn` abstraction that, in production, wraps the Executor's `DockerRunner.run_pytest` (network-none, read-only container). In tests this seam is mocked with canned exit codes — the suite never spins a real container or hits a real LLM. When you reason about a signal, remember the same sandbox boundary applies: a test that needs network will fail stability for the wrong reason.

**Signal fusion order** — the four numeric signals are computed first and assembled into the bundle; only then does the LLM see them alongside the test source and AC to judge semantic_relevance. The LLM is *informed by* the math, which is why a SURVIVED mutant tends to drag the LLM's relevance call down too — it can see the test didn't catch the mutation. This is intentional reinforcement, not double-counting.

**Why separation matters mechanically** — Gen-Functional has every incentive to declare success (its job is to produce a passing test). If it also graded quality, a test that imports-and-calls without asserting would score itself "done". Putting the verdict behind a separate agent with independent numeric signals removes that incentive entirely — the Evaluator has no stake in the test having been written.

---

## Common tasks
### Read a verdict and explain it
Open `findings/verdicts.json`, find the entry by `test_id`, and read the signals in this order: mutation → semantic_relevance → coverage_delta → stability → lint_promotion.
```jsonc
{
  "test_id": "test_login_rejects_expired_token",
  "verdict": "flag",
  "confidence": 0.52,
  "commit_readiness": false,
  "signals": {
    "coverage_delta": {"new_lines": 6},
    "stability": {"runs": 3, "passes": 3},
    "mutation": {"status": "SURVIVED"},      // <- the smoking gun
    "lint_promotion": {"promoted": []},
    "semantic_relevance": "medium"
  }
}
```
Here the test is green and covers new lines, but the SURVIVED mutant means it doesn't actually assert the behavior — hence `flag`, low confidence, not commit-ready.

### Diagnose a SURVIVED mutant
A SURVIVED mutant almost always means the test's assertions are too loose. Tighten them: assert on the *specific* return value / exception / side-effect that the mutated line controls, not just `assert result is not None`.

### Raise a test's confidence
Drive *multiple* signals up at once — confidence is a fusion, so fixing one rarely clears it:
- Add an assertion that kills the surviving mutant (mutation: SURVIVED → KILLED).
- Assert on the field the AC actually claims (semantic_relevance: low/medium → high).
- Ensure the test touches genuinely new lines (coverage_delta > 0).
- Remove `time.sleep` / freeze time (clears lint_promotion).

### Read flaky-history before trusting a green run
Check `<workspace>/<project>/test_history.json` for the test's flip-rate. A test that's flipped across past runs gets flagged even if this run's 3× stability passed.
```bash
cat ~/.tfactory/workspaces/<project_id>/test_history.json | python -m json.tool
```

### Decide accept vs flag vs reject
- All signals positive, semantic high → `accept`, high confidence, commit-ready.
- Signals disagree / one weak (SURVIVED, medium relevance) → `flag` for a human.
- Test adds no coverage AND survives mutation AND low relevance → `reject` (vacuous test).

### Walk a full verdict end-to-end
Trace how the five signals combine for a single test, `test_order_total_includes_tax`:
1. **coverage_delta** runs first — parses `coverage.xml`, finds the test covers 4 new lines in `compute_total`. Positive.
2. **stability** re-runs 3× via `runner_fn` (sandboxed) — 3/3 pass. Positive.
3. **mutate_probe** mutates the `return subtotal * (1 + tax_rate)` assertion's expected value — the test FAILS on the mutant → `KILLED`. Strong positive: the test actually asserts the tax math.
4. **lint_promotion** finds no promoted flake patterns. Neutral.
5. **semantic_relevance** — the LLM reads the AC ("order total includes tax") and the test, sees it asserts the taxed total, returns `high`.
All five align → `accept`, confidence ≈ 0.9, `commit_readiness: true`. Contrast with the earlier flagged example where only the mutant differed (SURVIVED) — that single weak signal halved the confidence and downgraded the verdict to `flag`.

### Compare two tests for the same AC
When the generator emits two candidates for one acceptance criterion, rank them by signal strength before the Triager dedups: prefer the one with a KILLED mutant and `high` relevance over a green-but-SURVIVED sibling, even if the latter has higher raw coverage. Coverage is the weakest of the five signals precisely because it's the easiest to inflate without asserting.

### Debug a verdict that "should" have been accepted
A test you believe is good landed as `flag`. Work the signals in priority order:
- Mutation SURVIVED → your assertion doesn't pin the behavior the mutated line controls. Tighten it.
- semantic_relevance `low`/`medium` → the test asserts something real but not the AC's *claim*. Re-read the AC and assert on its actual promise.
- coverage_delta zero → the lines you think you're testing were already covered; the test adds nothing new. Target uncovered behavior.
- stability < 3/3 → check for nondeterminism (unseeded random, real time, network reached inside the sandbox).
- lint_promotion non-empty → a flake pattern (`time.sleep`, `datetime.now`) was promoted; remove it.
Fixing the single weakest signal is usually enough to flip `flag` → `accept` because confidence is a fusion that rewards agreement.

### Interpret flaky-history vs a one-off pass
A test green this run but with a 40% historical flip-rate in `test_history.json` is *still* a quality problem. The Evaluator surfaces chronic flakiness even when the current 3× stability happened to pass, because committing a known-flaky test poisons the suite. Treat the flip-rate as a veto on commit_readiness, not a tiebreak.

### Explain a verdict to a reviewer
When a human asks "why did TFactory flag this?", translate the signals into plain language rather than dumping JSON:
> This test passes and is stable, but a mutation probe flipped the comparison in `apply_coupon` and the test still passed (SURVIVED) — so it isn't actually verifying the coupon logic. The LLM also rated its relevance to the AC as medium. Combined, that's a flag at 0.52 confidence: keep it, but a human should tighten the assertion before we commit it.
This framing makes the verdict actionable; the reviewer knows exactly which signal to chase.

### Set expectations about what each signal can prove
- coverage_delta proves *execution*, never *assertion*.
- stability proves *determinism this run*, never *correctness*.
- mutation proves the test *reacts to a behavior change* — the strongest evidence the test asserts something real.
- lint_promotion proves *flake risk*, not failure.
- semantic_relevance proves *alignment to the AC's claim*, judged by the LLM.
No single signal proves "good"; the verdict is their fusion, which is why a strong test scores high across several at once.

---

## Gotchas
1. **Green ≠ good** — a test can pass, be stable 3×, and still SURVIVE every mutant because it asserts nothing. Always read the mutation signal before trusting a pass.
2. **Coverage delta can be gamed** — a test that imports and calls a function bumps coverage without asserting anything. Coverage is necessary, never sufficient; pair it with mutation.
3. **One green stability run hides chronic flakiness** — `stability_runner` is per-run. The cross-run truth lives in `flaky_history.py` / `test_history.json`; consult it.
4. **Mutation routing is per-language** — don't expect Python's `mutate_probe` on TypeScript tests; the Evaluator dispatches via `mutation_dispatch.py` (Stryker for TS, PIT/Java is future). A "no mutation signal" can mean an unsupported language, not a weak test.
5. **The LLM judges relevance, not correctness** — semantic_relevance asks "does this verify the AC's claim", not "is the assertion arithmetically right". Don't read low relevance as "the test is wrong".
6. **commit_readiness ≠ accept** — an accepted test still won't be committed unless `commit_readiness` is true *and* the Triager's `TFACTORY_TRIAGER_GIT_WRITE` is opted in. Don't promise a commit from a verdict alone.
7. **Confidence is comparative, not calibrated probability** — treat 0–1 as a ranking/triage aid, not a literal "52% chance the test is good".
8. **A mutation signal can be absent for legitimate reasons** — `mutate_probe` mutates exactly ONE assertion; a test with no mutatable assertion (or an unsupported language) yields no KILLED/SURVIVED. Read "no mutation result" as "couldn't probe", not "weak test" — fall back harder on coverage + semantic relevance there.
9. **Stability failures are often environmental** — a 3× stability flip can mean a flaky test OR a flaky sandbox (a slow container, a missed dependency). Before blaming the test, confirm the run executed cleanly; the `runner_fn`/Docker boundary forbids network, so a test reaching out will "flake" deterministically.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Trusting a test because it's green | Passing tests routinely SURVIVE mutation and assert nothing | Read mutation + semantic_relevance before trusting a pass |
| Letting the generator self-grade | Self-validation lets confident-wrong tests through | Keep the Evaluator a separate agent (non-self-validation) |
| Optimizing only for coverage % | Coverage rewards execution, not assertion | Gate on mutation KILLED + semantic high, not just coverage |
| Treating one 3× stable run as "stable" | Chronic flakiness flips across runs, not within one | Consult `flaky_history.py` / `test_history.json` flip-rate |
| Rejecting any test with disagreeing signals | Reject deletes signal a human might want | Use `flag` for uncertainty; reserve `reject` for clearly vacuous/harmful tests |
| Reading confidence as a true probability | It's a fused triage score, not calibrated | Use it to rank/triage, not to make hard probabilistic claims |
| Overriding numeric signals with the LLM's call | The 4 numeric signals are the objective backbone | Let semantic_relevance complement, never overrule, the math |
| Promising a commit from an `accept` verdict | Commit needs commit_readiness + opted-in write flag | Check `commit_readiness` and the Triager dry-run flags first |
| Reading "no mutation result" as a weak test | The probe may not apply (no assertion / unsupported lang) | Distinguish SURVIVED from "couldn't probe"; lean on coverage + relevance |
| Blaming the test for every stability flip | Flips can be sandbox/env, not the test | Confirm a clean run; remember network is forbidden in the sandbox |
| Fixing one signal and re-checking | Confidence is a fusion; one fix rarely clears it | Drive multiple signals up together (mutation + relevance + coverage) |
| Committing a green-this-run but historically flaky test | Chronic flakiness poisons the suite | Veto commit_readiness on a bad `test_history.json` flip-rate |

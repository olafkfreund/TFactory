# test-strategy

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: test-strategy,test-pyramid,test-trophy,lanes,coverage,risk-based,prioritisation,unit,integration,browser,api,mutation

---

# Test Strategy: Choosing Lanes, Coverage, and What to Test

Use this skill when deciding *what* to test and *where* — picking TFactory lanes (unit · browser · api · integration · mutation) for a feature, setting a realistic coverage target, deciding what to test versus skip, and prioritising by risk. Triggers: a new feature lands on a branch and you must decide the lane mix; the Planner is emitting too many or too few subtasks; you are unsure whether to write a unit test or a browser test for a behaviour; coverage_target is being set in `.tfactory.yml` or an RFC-0002 `tfactory` block; you need to justify skipping a code path; a triage report is full of low-value accepted tests and you want to rebalance toward higher-signal lanes. This skill maps the test-pyramid-vs-trophy debate onto TFactory's concrete five-lane spine and the Evaluator's verdict model.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Strategy: Choosing Lanes, Coverage, and What to Test

A test suite is a portfolio, not a checklist. Every test costs generation time, sandbox execution time, and ongoing maintenance, and pays back in defect-detection and refactor-confidence. This skill is about allocating that budget across TFactory's lanes so the triage report carries signal, not noise. It is grounded in TFactory's actual architecture: the Planner emits lane-tagged subtasks, the Executor runs them in a Docker sandbox, and the Evaluator scores each with a 5-signal verdict.

---

## When to use this skill
- Deciding the lane mix (unit / browser / api / integration / mutation) for a feature on a branch.
- Setting `coverage_target` in `.tfactory.yml` or an RFC-0002 Task Contract `tfactory` block.
- The Planner produced 30 subtasks (the hard cap) and you need to cut to the load-bearing ones.
- Choosing between a fast unit test and a slow browser test for the same behaviour.
- Justifying *not* testing a code path (generated boilerplate, third-party glue, dead branches).
- Rebalancing a suite where the Evaluator keeps accepting trivially-true tests.

Do NOT trigger for:
- Application SAST/DAST/fuzz security testing — out of scope per DEC-002, delegated to dedicated pipelines.
- Cloud posture / CSPM assessment — that is the separate `cloud-discover` flow, not test generation.
- Writing the assertions themselves — see `assertion-design`.
- Turning a single AC into cases — see `acceptance-criteria-testing`.

---

## Key principles
1. **Risk × likelihood drives the budget, not line count.** Concentrate tests where a defect is both probable and expensive — auth, money, data mutation, irreversible side effects. A 100%-covered config loader is worth less than one strong test on a payment path.
2. **Prefer the cheapest lane that can actually catch the bug.** A bug in pure logic belongs in `unit`; a bug in the rendered DOM belongs in `browser`. Choosing a heavier lane than necessary wastes sandbox time and adds flake surface.
3. **Trophy over pyramid for feature-shaped work.** TFactory is browser-first and integration-heavy by design. For UI features, the bulk of value sits in `integration` + `browser`, with `unit` reserved for genuinely branchy logic. Don't force a tall unit pyramid onto a thin-logic feature.
4. **Coverage is a floor signal, never the goal.** `coverage_delta` is one of five Evaluator signals. A test that lifts coverage but dies to mutation (SURVIVED) is low-value. Target coverage to find *untested* code, then write tests that *kill mutants* there.
5. **One AC, one phase.** The Planner emits one phase per acceptance criterion. Keep that mapping clean — it makes coverage attributable to a requirement, not a line.
6. **Declared beats inferred.** If an RFC-0002 `tfactory` block declares lanes/frameworks/coverage_target/ac_to_code_map, that wins over the Planner's inference. Strategy work should land in the contract, not be re-derived each run.
7. **Mutation is the truth serum for "is this test worth keeping?"** Reserve the `mutation` lane for the modules where a surviving mutant would be a real, shippable bug — core domain logic, not getters.
8. **Skip loudly, not silently.** When you exclude a path, record *why* (boilerplate, vendor, unreachable). An undocumented gap looks identical to an oversight to the next agent.

---

## Core concepts
**The five lanes** — `unit` (isolated logic, no I/O: pytest / Jest / Vitest / JUnit5), `api` (HTTP/contract level against a running service), `integration` (multiple real components wired together, DB included), `browser` (Playwright / Cypress driving a real DOM), `mutation` (mutate the code under test, confirm a test fails). Lanes are tags on subtasks, not separate runners — the Executor dispatches each into the Docker sandbox.

**Pyramid vs Trophy** — The classic pyramid is unit-heavy. The "testing trophy" widens the integration band because integration tests catch the bugs that actually ship in wired-up systems. TFactory leans trophy: browser + integration carry the feature, unit covers the algorithmic core.

**The 5-signal verdict** — Each test gets `coverage_delta`, 3× `stability`, `mutate-and-check`, `flake-lint`, and LLM `semantic_relevance`, combined into accept / reject / flag plus a 0–1 confidence and cross-run flaky-history. Strategy choices should be made knowing the Evaluator will reject weak tests regardless of lane.

**Risk-based prioritisation** — Rank candidate behaviours by `impact × likelihood × detectability`. High-impact + likely-to-break + hard-to-detect-in-prod goes first and gets the strongest lane.

**Coverage delta vs absolute coverage** — TFactory measures the *delta* a test adds (Cobertura set math), not just the total. A test that re-covers already-covered lines adds zero delta and is a deduplication candidate.

**Framework-per-lane** — Python → pytest; TS/JS → Jest/Vitest (unit) + Playwright/Cypress (browser); Java → JUnit5 + JaCoCo (coverage) + PIT (mutation). The lane plus `subtask.language` picks the toolchain.

---

## Common tasks
### Pick the lane mix for a feature
Walk the acceptance criteria and bin each by where the behaviour lives:
- Pure calculation / validation / state machine → `unit`.
- "When I click X, the page shows Y" → `browser`.
- "POST /orders returns 201 with this body" → `api`.
- "Order persists and inventory decrements" → `integration`.
- "Our core pricing logic is correct under refactor" → add `mutation` on that module.

For a typical CRUD-with-UI feature, a sane starting split is roughly: 40% integration, 25% browser, 20% unit, 10% api, 5% mutation — then adjust to the actual logic depth.

### Set a realistic coverage_target
```yaml
# .tfactory.yml
coverage_target: 0.75   # delta-aware; aim for the changed surface, not the repo
lanes: [unit, integration, browser]
```
Set it against the *changed* code (the `base_ref..branch` diff), not the whole repo. 75–85% of the diff is realistic; 100% is a smell — it usually means tests on trivial lines.

### Cut a 30-subtask plan down to signal
The Planner hard-caps at 30 and warns at 15. When you hit the cap, drop in this order: (1) duplicate-behaviour subtasks (the dedup step would kill them anyway), (2) trivial getters/setters, (3) generated/boilerplate, (4) low-impact happy-path-only repeats. Keep every subtask that maps to a distinct AC.

### Encode strategy in the Task Contract
```yaml
# RFC-0002 tfactory block — wins over inference
tfactory:
  lanes: [unit, integration, browser, mutation]
  frameworks: { python: pytest, js: playwright }
  coverage_target: 0.80
  ac_to_code_map:
    AC-1: src/pricing/discount.py
    AC-2: src/web/checkout_page.tsx
```

### Decide what to skip
Skip: framework-generated boilerplate, thin DTO/getter shims, vendor glue with no branching, unreachable defensive branches. Test: anything with a conditional, a loop, money, time, or a side effect. Document each skip in the spec so it reads as intentional.

---

## Gotchas
1. **High coverage, all mutants survive.** Coverage went up but `mutate-and-check` reports SURVIVED across the board — the tests execute code without asserting on it. Fix: add behaviour assertions; see `assertion-design`. Coverage without mutation kill is theatre.
2. **Browser test for pure logic.** Driving a full Playwright session to check a number that a `unit` test could verify in milliseconds. Wastes sandbox time and adds flake. Push the assertion down a lane.
3. **Unit test that secretly hits the network/DB.** It's tagged `unit` but imports a real client — it'll flap in `--network=none` sandbox and fail 3× stability. Either mock it (stay `unit`) or retag `integration`.
4. **Coverage_target set against the whole repo.** A new feature can't move repo-wide coverage much; the target reads as "failed" forever. Always scope to the diff.
5. **Every AC forced into every lane.** One AC does not need a unit *and* api *and* browser test by default. Pick the lane where the AC's risk actually lives; add lanes only when the behaviour genuinely spans them.
6. **Ignoring flaky-history when choosing lanes.** A module with a bad cross-run flip-rate in `browser` won't get more reliable by adding more browser tests. Move the deterministic core down to `unit`/`integration` and keep browser thin.
7. **Treating mutation as a global gate.** Running the `mutation` lane over the whole diff is slow and noisy. Scope it to the modules where a surviving mutant is a real bug.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Chasing 100% coverage on the diff | Forces tests on trivial lines; mutants survive; triage fills with low-confidence accepts | Target 75–85% of changed code and pair coverage with mutation on core logic |
| Tall unit pyramid for a UI feature | Most defects ship in wiring/DOM, not isolated functions; misses the real risk | Lean trophy — weight `integration` + `browser`, reserve `unit` for branchy logic |
| One test per line of code | Conflates coverage with behaviour; brittle and high-maintenance | One test per *behaviour* / AC; let coverage_delta confirm reach |
| Picking the heaviest lane "to be safe" | Burns sandbox time, multiplies flake surface, slows the whole run | Cheapest lane that can catch the bug; escalate only when behaviour spans components |
| Re-deriving strategy every run | Inference drifts; plans become non-reproducible | Encode lanes/coverage/ac_to_code_map in the RFC-0002 `tfactory` block |
| Skipping paths without a note | Indistinguishable from an oversight; next agent re-investigates | Record each skip and its reason in the spec |
| Mutation lane on everything | Slow, noisy, dilutes the high-value mutant findings | Scope `mutation` to core domain modules where a surviving mutant ships a bug |
| Setting coverage_target as the success metric | Coverage is a floor signal, not quality; gameable | Treat the verdict (accept + confidence + mutation kill) as success |

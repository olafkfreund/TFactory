# acceptance-criteria-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: acceptance-criteria,gherkin,ears,ac-to-code-map,traceability,planner,phases,given-when-then,requirements

---

# Acceptance-Criteria Testing: From AC to Concrete Cases

Use this skill when turning acceptance criteria into concrete, runnable test cases — parsing Gherkin (`Given/When/Then`), EARS ("When <trigger> the system shall <response>"), or freeform markdown ACs into one phase per criterion, and wiring each phase to the code it verifies via `ac_to_code_map`. Triggers: a spec arrives with a list of acceptance criteria and you must decide how many tests each yields; the Planner is emitting phases and you need each tied to exactly one AC; an AC is vague or untestable and needs sharpening before generation; you are filling the `ac_to_code_map` in an RFC-0002 `tfactory` block; a triage report shows tests that don't trace back to any criterion; coverage looks fine but a stated requirement has no corresponding test. This skill is about traceability — every AC produces a test, every test names its AC.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Acceptance-Criteria Testing: From AC to Concrete Cases

Acceptance criteria are the contract between what was asked for and what was built. TFactory generates tests *aligned to* those criteria, which only works if each criterion is concrete, atomic, and mapped to code. This skill covers parsing the three AC dialects TFactory ingests, expanding each criterion into the right number of cases, and keeping the AC → test → code trace intact so the triage report can prove a requirement is verified.

---

## When to use this skill
- A spec lands with ACs (Gherkin / EARS / markdown) and you must derive test cases.
- The Planner emits phases and each must map to exactly one AC.
- An AC is ambiguous ("works correctly", "fast", "handles errors") and must be sharpened.
- Populating `ac_to_code_map` so each AC points at the file(s) under test.
- A triage report has tests that trace to no criterion (orphans) or ACs with no test (gaps).
- Using `spec_sources.py` to normalise a non-AIFactory AC source into `context/aifactory_spec.md`.

Do NOT trigger for:
- Choosing which lanes to run — see `test-strategy`.
- Boundary/equivalence case explosion within a single AC — see `boundary-and-equivalence-testing`.
- Writing the assertions — see `assertion-design`.
- Inventing requirements the spec doesn't state — ACs come from the spec, never from imagination.

---

## Key principles
1. **One AC → one phase.** The Planner emits one phase per acceptance criterion. This is the unit of traceability; never merge two ACs into one phase or split one AC across phases without reason.
2. **An untestable AC is a bug in the spec, not the test.** "Should be performant" has no pass/fail. Sharpen it to a measurable assertion ("p95 < 200ms under N concurrent requests") before generating, or flag it back.
3. **Every test names its AC.** Tag each generated test with its criterion (in the test id / docstring) so the triage report can show AC coverage, not just line coverage.
4. **The map wins over inference.** `ac_to_code_map` in the RFC-0002 `tfactory` block tells the Planner exactly which file an AC verifies — this beats the Planner guessing from the diff.
5. **One AC usually yields several cases.** A single criterion expands into happy path + error path + boundaries. The AC is the phase; the cases are the tests inside it.
6. **Normalise before you plan.** Gherkin, EARS, and markdown all reduce to the canonical `context/aifactory_spec.md` via `spec_sources.py`. Plan from the normalised form, not the raw source.
7. **Trace both directions.** Every AC must have ≥1 test (no gaps) and every test must map to an AC (no orphans). A gap is a missed requirement; an orphan is unaccountable work.
8. **The AC's verb is the assertion.** "the system *shall reject*" → assert a rejection. "*displays* the total" → assert the rendered total. Don't lose the verb in translation.

---

## Core concepts
**Gherkin** — `Given` (precondition / fixture), `When` (the action under test), `Then` (the assertion). One scenario ≈ one test case. `Scenario Outline` + `Examples` is a parameterised table → one parametrised test with a row per example.

**EARS** — Easy Approach to Requirements Syntax. Patterns: ubiquitous ("The system shall…"), event-driven ("When <trigger>, the system shall <response>"), state-driven ("While <state>, …"), unwanted ("If <condition>, then the system shall <response>"), optional ("Where <feature>, …"). The trigger maps to the `When`/arrange, the response to the `Then`/assert.

**Markdown ACs** — Freeform bullet lists ("- Users can reset their password via email link"). Least structured; you infer the When/Then. Sharpen these first.

**`ac_to_code_map`** — Declared mapping `AC-id → file(s)`. Tells the Planner which code each criterion exercises, scoping coverage_delta to the right surface and preventing the Planner from testing unrelated lines.

**Phase** — The Planner's unit of work, one per AC. Inside a phase, Gen-Functional writes one or more test files (the cases). Status flows generating → generated per phase.

**Traceability matrix** — The AC × test grid the triage report can render: which ACs are covered, by how many tests, and with what verdict.

---

## Common tasks
### Convert a Gherkin scenario to a test case
```gherkin
Scenario: Reject expired discount code
  Given a discount code "SUMMER" that expired yesterday
  When the customer applies "SUMMER" at checkout
  Then the order total is unchanged
  And an "expired code" message is shown
```
→ One `browser` or `api` test: arrange the expired code (fixture/seed), act = apply at checkout, assert total unchanged AND message present. Two `Then`s → two assertions in one behaviour-focused test (same behaviour: rejection).

### Convert an EARS requirement
```
AC-3: When a withdrawal exceeds the daily limit, the system shall
      decline the transaction and log an audit event.
```
→ `integration` test: arrange account near limit, act = withdraw over limit, assert (1) decline returned, (2) audit row written. The "and log" clause is a real, separate observable side effect — assert it.

### Sharpen a vague markdown AC
Raw: "- Login should be secure and fast." Untestable. Sharpen to:
```
AC-7a: After 5 failed logins within 10 min, the account is locked for 15 min.
AC-7b: A successful login responds within 300ms (p95) under 50 RPS.
```
Each is now a phase with a concrete assertion.

### Populate ac_to_code_map
```yaml
tfactory:
  ac_to_code_map:
    AC-1: [src/auth/login.py, src/auth/lockout.py]
    AC-2: src/web/checkout_page.tsx
    AC-3: src/banking/withdrawal.py
```

### Check for gaps and orphans
After a run, cross the verdicts against the AC list: any AC with zero tests is a **gap** (regenerate); any test mapping to no AC is an **orphan** (it may be a deduplication target or an over-eager addition).

---

## Gotchas
1. **Compound ACs.** "User can create, edit, and delete a post" is three ACs wearing a trenchcoat. Split into three phases, or coverage attribution and dedup both break.
2. **The hidden side-effect clause.** "…and sends a confirmation email" is a second observable behaviour. Dropping it produces a test that passes while the email never sends. Assert every clause that's observable.
3. **EARS "shall" lost in translation.** The modal verb is the assertion. "shall reject" must become an assertion that the rejection *happened*, not just that the function ran.
4. **Vague AC generated anyway.** If you let "works correctly" through, the LLM invents an assertion — which the Evaluator's `semantic_relevance` may flag as not tied to the AC. Sharpen first.
5. **Planning from raw source.** Gherkin tables and EARS state clauses get mis-parsed if you skip `spec_sources.py` normalisation. Always plan from `context/aifactory_spec.md`.
6. **Orphan tests inflating the count.** Tests the LLM adds "for good measure" that map to no AC look like coverage but answer no requirement — and may be deduped away. Keep tests AC-anchored.
7. **One mega-test per AC with five `Then`s about different behaviours.** That's five behaviours, not one. Split — see `assertion-design` on one-behaviour-per-test.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Merging multiple ACs into one phase | Breaks one-AC-one-phase traceability; coverage can't be attributed | One phase per criterion; split compound ACs first |
| Generating from a vague AC ("fast", "secure") | No pass/fail; the LLM invents assertions the Evaluator flags | Sharpen to a measurable assertion before generation |
| Ignoring "and …" side-effect clauses | Test passes while a real behaviour (email, audit log) is broken | Assert every observable clause in the AC |
| Tests with no AC tag | Triage can't build the traceability matrix; orphans look like coverage | Tag every test with its AC id in the docstring/test id |
| Letting the Planner guess the target file | Coverage_delta measures the wrong surface; wastes a phase | Declare `ac_to_code_map` in the RFC-0002 `tfactory` block |
| Planning from raw Gherkin/EARS | Tables and state clauses mis-parse; cases get dropped | Normalise via `spec_sources.py` to the canonical spec first |
| Dropping the AC's modal verb | "shall reject" becomes "calls the function"; bug ships green | Map the verb to the assertion verb directly |
| One test covering five different behaviours of one AC | Mutation/dedup/diagnosis all suffer; first failure hides the rest | One behaviour per test even within a single AC's phase |

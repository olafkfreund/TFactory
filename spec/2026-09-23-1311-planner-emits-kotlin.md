---
status: draft
issue: 1311
intent: intent/2026-09-23-1311-planner-emits-kotlin.md
---

# Spec: the Planner can emit Kotlin subtasks

## What the measurement changed about the shape of this fix

`_validate_framework_consistency` in `agents/planner.py:238` already validates
every subtask's `(language, framework, lane)` **against the framework
registry**, not against a hardcoded list. It rejects a framework that is not in
the registry, a language that disagrees with the descriptor, and a lane the
descriptor does not declare.

So the single source of truth exists and is enforced at the gate. Only the
Planner *prompt* carries a second, hand-maintained copy of the same vocabulary.
That makes this a drift fix with a small centre: **drop a Kotlin descriptor, and
render the prompt's vocabulary from the registry that already validates it.**

It also means Kotlin fails closed today rather than silently: a subtask with
`framework: "gradle"` is rejected with `invalid_framework`, because no such
descriptor exists. Nothing in the chain is load-bearing on the absence.

## Design

### 1. `frameworks/gradle/descriptor.yaml` (new)

A framework descriptor for Kotlin, following `frameworks/go-test/descriptor.yaml`:

- `name: gradle`, `language: kotlin`, `lanes: [unit]` — unit only, matching
  `languages/kotlin.yaml`, where browser and integration are `available: false`
  with mandatory RFC-0006 reasons. The validator then rejects a Kotlin browser
  subtask by construction.
- `manifest_signals`: Kotlin-specific only — `settings.gradle.kts`,
  `build.gradle.kts:kotlin`, `gradle/libs.versions.toml:kotlin`. Deliberately
  **not** bare `build.gradle`/`build.gradle.kts`: `frameworks/junit` already
  claims both, and manifests are trusted only when they name exactly one
  language, so duplicating them would turn a weak-but-working Java signal into
  an ambiguous one for both.
- `test_path_conventions: ["src/test/kotlin/**/*Test.kt", "**/src/test/kotlin/**/*.kt"]`
  — Gradle's convention, under the module `_gradle_module_dir` resolves.
- `context_block`: the Kotlin generation guidance the issue asks for — JUnit 5
  (`kotlin.test` / `org.junit.jupiter`), one `@Test fun` per behaviour, assert a
  boundary or error case, no new dependencies, no `Thread.sleep` for
  synchronisation.
- `coverage_strategy: skip` — the Nix Gradle lane merges JUnit XML and collects
  no coverage (Factory#1712). Claiming `jacoco` here would invite a coverage
  signal nothing produces.
- `runtime.image`: `runtime.image` is required and non-empty by
  `framework_registry/validator.py`. `go-test` sets an image while documenting
  that the Nix-per-task substrate actually supplies the toolchain; Kotlin
  follows that precedent with the same comment, so no schema change is needed.

### 2. The prompt's vocabulary comes from the registry

`prompts_pkg/prompts.py`:

- **`_EXT_LANGUAGE` is derived** from the registry's `test_path_conventions`:
  the suffix of each glob maps to that descriptor's language, and an extension
  claimed by two languages is dropped as ambiguous (`.feature` is claimed by
  cucumber/typescript and karate/java).
- **A descriptor-less fallback stays, and is bounded.** There is no Rust
  framework descriptor, so pure derivation would lose `.rs -> rust` and regress
  #443. The fallback keeps exactly the entries no descriptor can supply, and a
  test asserts the two sets are disjoint — so a language cannot be defined in
  both places, which is the drift this change exists to remove.
- **`_AC_COMMAND_LANGUAGE` moves into the descriptors** as a new optional
  `ac_command_tokens` field (`gradle test`, `./gradlew test` for Kotlin;
  `go test`/`go build`, `pytest`, `jest`, `npm test`, `vitest` move to their
  own descriptors). Optional, so every existing descriptor stays valid.
  Rust's tokens stay in the same bounded fallback as `.rs`.
- **The FRAMEWORK REGISTRY block gains each framework's test path
  conventions**, so the prompt's file-naming rule can point at the descriptor
  instead of restating conventions per language.

`apps/backend/prompts/planner.md`:

- The subtask schema's `language` and `framework` values become a pointer to
  the injected FRAMEWORK REGISTRY block rather than a closed list.
- Step 0 and Step 3 keep their algorithm but stop enumerating languages; the
  per-language lines become "use the `(language, framework, lane)` row the
  registry lists for the detected language".
- Rule 6's per-framework `files_to_create` conventions point at the conventions
  now rendered in the registry block. The worked examples stay: they are
  illustrative, and removing them would cost more prompt quality than the drift
  they carry.

### 3. Nothing changes in the evaluator

`_completed_kotlin_subtasks` already partitions on `language == "kotlin"` and
lane `unit`/`functional`, and `_resolve_kotlin_runner_fn` already calls
`run_gradle_lane_via_nix`. The descriptor emits exactly `kotlin`, so the
evaluator matches the literal string it already matches. The descriptor's
aliases (`android`, `kts`) are a detection concern in `languages/kotlin.yaml`
and are deliberately not emitted as subtask languages.

## Alternatives rejected

- **Add Kotlin to each hardcoded list** (intent option a). Smallest diff, but
  the registry already validates what the prompt re-declares; leaving two
  copies is what produced this issue, and the next language repeats it.
- **Widen `frameworks/junit` to the JVM.** One descriptor fewer, but it changes
  what Java planning is told, and its `tfactory-runner-java:latest` runtime is
  the docker-host substrate the in-cluster Kotlin lane does not use. Rejected at
  intent review.
- **Derive `_EXT_LANGUAGE` purely from descriptors, with no fallback.**
  Measured to regress: no Rust descriptor exists, so `.rs` would stop pinning
  Rust.
- **Emit `language: "android"`/`"kts"` for Kotlin variants.** The evaluator
  partitions on the literal `kotlin`; anything else re-creates the silent drop.
- **Generate `planner.md` wholesale from descriptors.** A repo-owned prompt
  that a generator overwrites is the failure shape recorded in
  `repo-owned-flake-beats-generation`; injecting a block next to a hand-written
  prompt keeps both readable.
- **Add an in-cluster Java lane here.** Split to #1321 at intent review.

## Risks

- **The language-pinning path is shared.** A derivation that silently loses an
  extension would mis-pin an unrelated language. Mitigated by asserting the
  derived map equals today's table plus Kotlin's additions, exactly — a
  set-equality assertion, not a spot check.
- **Prompt changes move LLM behaviour and cannot be unit-tested.** Mitigated by
  rendering the full prompt for Python, TypeScript, Go and Java fixtures before
  and after, and requiring the diff to be confined to the registry block's new
  rows; plus the live Kotlin run as the only behavioural claim.
- **`junit` already claims `build.gradle.kts`.** If Kotlin's manifest signals
  duplicated it, both languages would match and the manifest signal would be
  discarded as ambiguous, weakening Java detection. Mitigated by the
  Kotlin-specific signals above, and a test asserting no manifest signal is
  claimed by two descriptors.
- **Prompt size.** The registry block grows by one row plus conventions. Bounded
  and measured in the tests below; if it grows materially the conventions can be
  rendered only for the detected language.
- **`ac_command_tokens` is new schema surface.** Optional and additive; the
  validator must accept descriptors without it, asserted by keeping one
  descriptor (portal-ui) without the field.
- The whole change is inert for every non-Kotlin task if the derivation is
  correct, which is precisely what the before/after prompt diff proves.

## Verification

Deterministic, in CI:

1. `frameworks/gradle/descriptor.yaml` loads and validates; the registry then
   contains `gradle: language=kotlin, lanes=[unit]`.
2. `_validate_framework_consistency` **accepts** a plan whose subtask is
   `(kotlin, gradle, unit)` and **rejects** `(kotlin, gradle, browser)` and
   `(kotlin, junit, unit)`. Mutation: removing the descriptor makes case one
   fail with `invalid_framework`.
3. The derived extension map equals the current table plus `.kt`/`.kts`, by set
   equality. Mutation: deleting `go-test`'s conventions from the fixture
   registry must fail the test, proving the assertion reads the derivation.
4. `_language_from_files(['app/src/main/kotlin/Calc.kt'])` returns `kotlin`
   (today: `None`), and `.py`/`.go`/`.ts`/`.rs` answers are unchanged.
5. An acceptance criterion saying `gradle test` pins Kotlin.
6. The rendered planner prompt for Python, TypeScript, Go and Java fixtures
   differs from today's only inside the FRAMEWORK REGISTRY block, and every
   removed line is one whose fact is now rendered from the registry.
7. No extension and no manifest signal is claimed by two descriptors.
8. Each new test module collected alone, per the repo's collection-order rule.

Live, in-cluster (the intent's approved proof):

9. A Kotlin fixture repo is driven through the **real Planner**, producing a
   `test_plan.json` whose unit subtask carries `language: "kotlin"`,
   `framework: "gradle"`, and a `files_to_create` path under
   `src/test/kotlin/`. The plan passes the post-emit validator unmodified.
10. That plan runs through the evaluator into `run_gradle_lane_via_nix`: the
    fixture's suite passes with real counts, and the subtask appears in the
    bundles with a verdict read from the merged JUnit report.
11. **Mutation:** one assertion in the fixture is broken and the same path is
    re-run; the verdict must flip to failed with the failing test named. A
    pass-shaped result (zero tests, missing report) counts as a failure of this
    spec, per Factory#1712's evidence rule.

Gates: `ruff` + `ruff format` (pinned), `scripts/ratchet_lint.py --base
origin/dev` with its `--package` flags, the hub security-sinks lint whole-repo,
and the full `tests/` run for the touched modules.

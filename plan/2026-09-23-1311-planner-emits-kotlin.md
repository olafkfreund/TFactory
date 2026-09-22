---
status: approved
issue: 1311
spec: spec/2026-09-23-1311-planner-emits-kotlin.md
---

# Plan: the Planner can emit Kotlin subtasks

## Approved decisions (self-contained)

- **Why.** TFactory 0.9.27 ships the in-cluster Kotlin/Gradle lane
  (`run_gradle_lane_via_nix`), and the evaluator routes `language == "kotlin"`
  to it, but nothing emits such a subtask. Measured on `origin/dev` at
  `12411d0e`: `planner.md:59` allows only `python|typescript|go|rust`;
  `_EXT_LANGUAGE` has no `.kt` (`['…/Calc.kt'] -> None`); `_AC_COMMAND_LANGUAGE`
  has no `gradle test`; the only JVM framework is `junit: language=java`, whose
  `manifest_signals` claim `build.gradle` **and** `build.gradle.kts`, so a
  Kotlin repo reads as Java and the evaluator's literal `kotlin` match misses.
- **The centre of the fix.** `_validate_framework_consistency`
  (`agents/planner.py:238`) already validates `(language, framework, lane)`
  against the framework registry. The registry is the single source of truth and
  is enforced; only the prompt keeps a second copy. So: drop a Kotlin
  descriptor, and render the prompt's vocabulary from that same registry.
- **Kotlin gets its own framework descriptor** (`frameworks/gradle/`),
  `language: kotlin`, `lanes: [unit]`. `frameworks/junit` is not touched, so
  Java planning cannot regress.
- **Derivation with a bounded fallback.** `_EXT_LANGUAGE` and the AC command
  tokens come from descriptors; entries no descriptor can supply (Rust — there
  is no Rust framework descriptor) stay in an explicit fallback, and a test
  asserts the two sets are disjoint.
- **Kotlin only.** Java's identical gap is #1321.
- **Proof** is a Kotlin fixture driven through the real Planner in-cluster into
  the Gradle lane, plus a mutation that must flip the verdict. A full PARR run
  is not required.
- **Constraints.** Emit the literal `kotlin` (not `android`/`kts`). Keep
  Kotlin's unavailable lanes unavailable. Do not regress Python, TypeScript, Go
  or Java: prove it by diffing the rendered prompt, not by assertion.

## Steps

Branch `fix/1311-planner-kotlin` off `dev`, one commit per step.

1. **Kotlin framework descriptor** `frameworks/gradle/descriptor.yaml`:
   `name: gradle`, `language: kotlin`, `lanes: [unit]`,
   `version_range` for Gradle 8.x, `runtime.image`
   `tfactory-runner-kotlin:latest` with the `go-test` comment that the Nix
   per-task substrate actually supplies the toolchain,
   `manifest_signals: [settings.gradle.kts, build.gradle.kts:kotlin,
   gradle/libs.versions.toml:kotlin]` (deliberately not bare `build.gradle*`,
   which `junit` claims), `test_path_conventions:
   [src/test/kotlin/**/*Test.kt, **/src/test/kotlin/**/*.kt]`,
   `coverage_strategy: skip`, and a `context_block` with the JUnit 5 /
   `kotlin.test` generation rules.
   → verify: `load_registry()` returns `gradle` with
   `language=kotlin, lanes=[unit]`; `_validate_framework_consistency` accepts
   `(kotlin, gradle, unit)` and rejects `(kotlin, gradle, browser)` and
   `(kotlin, junit, unit)`. **Mutation:** delete the descriptor file and the
   accept-case must fail with `invalid_framework`.

2. **Optional `ac_command_tokens` in the descriptor schema**
   (`framework_registry/descriptor.py`, `validator.py`): a tuple of command
   substrings, defaulting to empty so every existing descriptor stays valid.
   Populate it in `go-test` (`go test`, `go build`), `pytest` (`pytest`),
   `jest` (`jest`, `npm test`), `vitest` (`vitest`) and `gradle`
   (`gradle test`, `./gradlew test`). Leave `portal-ui` without the field.
   → verify: a descriptor without the field loads; each populated descriptor
   exposes its tokens; the validator rejects a non-list value.

3. **Derive the vocabulary** in `prompts_pkg/prompts.py`: build
   `_EXT_LANGUAGE` from each descriptor's `test_path_conventions` suffixes
   (dropping any extension claimed by two languages) and the AC token table
   from `ac_command_tokens`, each merged over a small explicit
   `_NO_DESCRIPTOR_LANGUAGES` fallback holding only Rust's `.rs`, `cargo test`
   and `cargo build`.
   → verify: the derived extension map equals today's table plus `.kt`/`.kts`,
   by set equality; `_language_from_files(['app/src/main/kotlin/Calc.kt'])`
   returns `kotlin` where it returned `None`; `.py`/`.go`/`.ts`/`.tsx`/`.js`/
   `.jsx`/`.rs` are unchanged; an AC saying `gradle test` pins Kotlin; the
   derived and fallback key sets are disjoint. **Mutation:** removing
   `go-test`'s `test_path_conventions` from a fixture registry must fail the
   set-equality test, proving it reads the derivation and not the old table.

4. **Render the registry block with conventions**
   (`_build_framework_registry_block`): add each framework's
   `test_path_conventions` to its row.
   → verify: the block contains the `gradle` row with its Kotlin conventions;
   the rendered size increase is asserted under a bound.

5. **Point `planner.md` at the registry** instead of restating it: the schema's
   `language`/`framework` values become a pointer to the injected FRAMEWORK
   REGISTRY block; Step 0 and Step 3 keep the algorithm but stop enumerating
   languages; Rule 6 defers to the rendered conventions. The worked examples
   stay.
   → verify: the full rendered prompt for Python, TypeScript, Go and Java
   fixtures differs from `origin/dev`'s only inside the registry block, and
   every removed line's fact is present in the rendered block. Captured as a
   before/after diff in the PR body.

6. **Live in-cluster proof** (before the PR, as evidence), mirroring
   Factory#1712's method — run the new code in the pod, change no files in it:
   - stage a Kotlin fixture project (a Gradle module plus a spec naming one
     acceptance criterion) under the `tfactory-data` mount, with a scratch
     `spec_dir`;
   - call `run_planner(spec_dir, project_dir)` in the pod and read the emitted
     `test_plan.json`: a unit subtask must carry `language: "kotlin"`,
     `framework: "gradle"`, and a `files_to_create` path under
     `src/test/kotlin/`, and must pass the post-emit validator unmodified;
   - run that plan through the evaluator into `run_gradle_lane_via_nix`: real
     test counts, the subtask present in the bundles with a verdict read from
     the merged JUnit report;
   - **mutation:** break one assertion in the fixture and re-run; the verdict
     must flip to failed with the failing test named. Zero tests or a missing
     report counts as a failure, not a pass;
   - delete the scratch dirs.

7. **Gates:** `ruff check` + `ruff format --check` (pinned),
   `scripts/ratchet_lint.py --base origin/dev --package apps/backend
   --package apps/web-server --package scripts`, the hub security-sinks lint
   over the whole repo, the full `tests/` run for the touched modules, and each
   new test module collected **alone**.

8. **PR → `dev`** with the step 6 evidence and the step 5 prompt diff. Merge
   when green and threads are resolved.

9. **Close out:** close #1311 with the live evidence, note #1321 (Java) as the
   remaining sibling gap, and update memory with what the measurement showed
   (the validator was already descriptor-driven; the prompt was the only copy).

## Deviations recorded during implementation

- **Step 3 — the derivation source changed.** The spec said to derive
  `_EXT_LANGUAGE` from each descriptor's `test_path_conventions`. Measured, that
  is unsafe: those globs describe TEST paths, and the derivation yields
  `.json -> cloud` (from the cloud frameworks' findings globs), which would
  mis-pin any repo whose changed files include a `package.json`. It also yields
  `.java -> java`, moving Java's behaviour, which the approved scope excludes.
  `.kts` is not derivable from test globs at all.
  Instead, descriptors declare an explicit optional `source_extensions`, the
  same additive pattern as `ac_command_tokens` (step 2). The "one engine"
  property is unchanged — the vocabulary still lives on the descriptor — but it
  is declared rather than inferred. `frameworks/junit` deliberately declares
  none, so Java pins exactly as before (`.java -> None`), and #1321 owns that
  gap. The step 3 mutation is correspondingly "blank Kotlin's
  `source_extensions`" rather than "remove go-test's `test_path_conventions`";
  it fails two tests, as required.

- **Step 5 — what the prompt diff proves.** The spec said the rendered prompt
  must differ "only inside the FRAMEWORK REGISTRY block". That cannot hold:
  step 5's whole purpose is to rewrite the prose that restated the registry, so
  `planner.md` changes too. The property actually verified is stronger and
  language-independent: the prose diff is **byte-identical for every language**,
  and the deterministic DETECTED PROJECT LANGUAGE pin — the block that decides
  behaviour — is byte-identical for Python, TypeScript and Go, unchanged for
  Java, and changes only for Kotlin (from "no deterministic language signal" to
  kotlin + gradle). Captured as tests rather than a one-off diff.
  The registry rows also gained `detects=` and `ac=`, so every fact the deleted
  prose stated is still in the prompt, rendered from the descriptors.

## Tests

```sh
# TFactory, from the repo root
apps/backend/.venv/bin/python -m pytest tests/ -q -k "framework_registry or planner_prompt or language_pin"
apps/backend/.venv/bin/python -m pytest tests/test_<new_module>.py -q   # each new module alone
apps/backend/.venv/bin/ruff check apps/backend tests scripts
apps/backend/.venv/bin/ruff format --check apps/backend tests scripts
apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/dev \
  --package apps/backend --package apps/web-server --package scripts
```

Expected: every new test fails before its step's code and passes after; the
three mutations named in steps 1 and 3 fail as described; the rendered prompt
for Python, TypeScript, Go and Java changes only inside the registry block; the
live run reports real counts, and the mutated run fails with the test named.

## Rollback

- Revert the PR. The prompt returns to its hardcoded lists and the derivation
  disappears; `frameworks/gradle/descriptor.yaml` goes with it, so
  `(kotlin, gradle, unit)` is rejected by the post-emit validator again and no
  Kotlin subtask can be emitted — the pre-#1311 behaviour. The Kotlin lane
  itself (Factory#1712) is untouched and still reachable directly.
- No data migration, no deployment state, and nothing outside TFactory changes.
  `frameworks/junit` is never edited, so Java planning is unaffected either way.

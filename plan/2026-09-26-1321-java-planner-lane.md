---
status: approved
issue: 1321
spec: spec/2026-09-26-1321-java-planner-lane.md
---

# Plan: Java's unit lane runs in-cluster through Maven

## Approved decisions (self-contained)

- **Why.** `.java` maps to no language (`_language_from_files(['…/Calc.java'])`
  → `None`), no AC token names Java, and the only JVM framework
  (`frameworks/junit`) names a docker-host image this cluster cannot run. So
  Java is unpinned, and fixing only the vocabulary would plan subtasks that
  cannot execute — #1311's failure shape.
- **Maven, not Gradle.** `_LANG_ATTRS["java"] = ["jdk21", "maven"]` already
  provisions the toolchain; gradle is deliberately absent, and adding it means a
  hub PR plus re-vendoring four services. Maven also matches `junit`'s existing
  `mvn -B test` entrypoint and `pom.xml` signals.
- **A sibling lane, not a generalised one.** `maven_job_script`,
  `java_environment` and `run_maven_lane_via_nix` sit beside the Kotlin trio and
  share `_junit_counts` and the evidence rule. Kotlin's path stays byte-identical.
- **`frameworks/maven` is a new descriptor**; `frameworks/junit` drops `unit` and
  keeps `api`. `lang_registry`'s `java.unit` moves to maven with
  `available_at_mvp=True`.
- **No `prompts.py` edit.** #1311 made the vocabulary descriptor-derived, so
  `.java` and the `mvn` tokens arrive from the new descriptor.
- **Proof** is a live in-cluster run with real counts plus a mutation that flips
  the verdict, and a real Planner run emitting `(java, maven, unit)`.

## Steps

Branch `feat/1321-java-maven-lane` off `dev`, one commit per step.

1. **Check nothing depends on `(java, junit, unit)`** before changing it: grep
   the repo and the workspaces for plans pinning that triple.
   → verify: the grep's output is recorded in the PR; if any exist, the plan is
   revised before proceeding.
2. **Maven fixture** `tests/fixtures/maven-min/`: `pom.xml` (JUnit 5, surefire),
   `src/main/java/Calc.java`, `src/test/java/CalcTest.java` with 3 tests.
   → verify: locally in a nix shell with `jdk21 maven`, `mvn -B test` passes with
   3 tests and writes `target/surefire-reports/TEST-*.xml`.
3. **`maven_job_script` + `java_environment` + `_maven_module_dir`** in
   `nix_env.py`.
   → verify: unit tests for the script's contents (`mvn -B test`, the
   `__MAVEN_EXIT` marker, writable `-Dmaven.repo.local`, quoted paths), the
   merge snippet run over two sample XML files, and the environment naming only
   the language.
4. **`run_maven_lane_via_nix`.**
   → verify: returns `None` with the runner image unset; the evidence rule turns
   a zero exit with no report into returncode 1.
5. **`frameworks/maven/descriptor.yaml`**, and `junit` drops `unit`.
   → verify: the registry offers `maven: language=java, lanes=[unit]`;
   `_validate_framework_consistency` accepts `(java, maven, unit)` and rejects
   `(java, junit, unit)` and `(java, maven, browser)`; the derived vocabulary
   gains `.java` and the `mvn` tokens while every other language's pin is
   unchanged.
6. **Evaluator wiring:** `_completed_java_subtasks`, `_resolve_java_runner_fn`,
   the `java` branch in `_build_all_bundles`, and `lang_registry`'s java.unit
   move.
   → verify: a `language=java` unit subtask reaches the Maven runner, not the
   pytest batch and not Gradle. **Mutation:** point it at the Gradle runner and
   the test must fail.
7. **Live in-cluster proof**, the #1712/#1311 method — run the new code in the
   pod, change no files in it:
   - stage the fixture under the `tfactory-data` mount with a scratch spec dir;
   - call `run_maven_lane_via_nix`: returncode 0, `junit.xml` with
     `tests="3" failures="0"`;
   - record how long a cold local repository takes, and raise the timeout
     deliberately if it is close to the limit;
   - **mutation:** break one assertion, re-run, require returncode 1 with the
     failing test named;
   - delete the scratch dirs.
8. **Live Planner proof:** a Java fixture driven through the real Planner emits
   `(java, maven, unit)` at `src/test/java/...` and passes the post-emit
   validator unmodified.
9. **Gates:** ruff, ruff format over the CI path list, `ratchet_lint.py --base
   origin/dev` with its `--package` flags, the hub security-sinks lint, the full
   suite, and each new test module collected alone.
10. **PR → `dev`** with the step 7 and 8 evidence; close #1321; file the Gradle
    follow-up (one line in `_LANG_ATTRS`, hub-side).

## Tests

```sh
apps/backend/.venv/bin/python -m pytest tests/test_nix_env.py -q -k "maven or java"
apps/backend/.venv/bin/python -m pytest tests/ -q -k "evaluator and java"
apps/backend/.venv/bin/python -m pytest tests/test_framework_registry.py tests/test_planner.py -q
apps/backend/.venv/bin/python -m pytest tests/test_planner_language_vocabulary.py -q
apps/backend/.venv/bin/ruff check apps/backend tests scripts
apps/backend/.venv/bin/ruff format --check apps/backend apps/web-server scripts tests
apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/dev \
  --package apps/backend --package apps/web-server --package scripts
```

Expected: each new test fails before its step and passes after; the routing
mutation fails; Kotlin's tests are untouched; the live run reports 3/0 then
non-zero on mutation.

## Rollback

Revert the PR. Java returns to unpinned (`.java` → `None`), `frameworks/junit`
regains its `unit` lane, and `lang_registry`'s java.unit returns to
`available_at_mvp=False`. The Kotlin lane is untouched either way, and there is
no persistent state to unwind — the lane is stateless per run.

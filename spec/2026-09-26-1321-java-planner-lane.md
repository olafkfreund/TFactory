---
status: approved
issue: 1321
intent: intent/2026-09-26-1321-java-planner-lane.md
---

# Spec: Java runs its unit lane in-cluster through Maven

## What the measurements settled

| Question | Measured answer |
| --- | --- |
| Is a hub language descriptor needed? | **No.** `_LANG_ATTRS["java"] = ["jdk21", "maven"]` already provisions the toolchain, and `_descriptor_for_language` returns `None` for anything in that table, so the builtin branch owns Java. |
| Gradle or Maven? | **Maven.** Gradle is deliberately absent from Java's attrs, and adding it means a hub PR plus re-vendoring four services. Maven also matches `frameworks/junit` (`mvn -B test`, `pom.xml` signals). |
| Does anything over-claim a Java unit lane? | `lang_registry` already has `java.unit = ToolSpec("junit", ..., available_at_mvp=False)`, so the registry is honest. The over-claim is `frameworks/junit`'s `runtime.image: tfactory-runner-java:latest` — a docker-host image this cluster cannot run. |
| Is the Gradle lane's shape reusable? | Yes in structure, not in code: it copies the module to a writable path, runs the build, recovers the exit code through an `echo __GRADLE_EXIT=$?` marker, then merges `*/build/test-results/*/TEST-*.xml` under one `<testsuites>` root. Maven's Surefire writes the same per-class JUnit XML to `target/surefire-reports/`. |

## Design

### 1. `maven_job_script`, beside `gradle_job_script` in `agents/nix_env.py`

Same five moves as the Gradle script, with Maven's paths:

- copy the module to a writable `/tmp` path (`/work` is read-only to the Job's
  uid — the reason the Gradle lane does this);
- `mvn -B test` with `-Dmaven.repo.local=<writable>` so the local repository is
  not `$HOME/.m2` inside a read-only home, mirroring how the Gradle lane sets
  `GRADLE_USER_HOME`;
- `echo __MAVEN_EXIT=$?` to recover the real exit code through the pipe;
- merge `*/target/surefire-reports/TEST-*.xml` into one `<testsuites>` at
  `<stage>/junit.xml`;
- reuse the existing `_junit_counts` / evidence check unchanged: a zero exit with
  no report, zero tests, or recorded failures becomes returncode 1.

Every path is `_shquote`d, as #1712's review required.

### 2. `java_environment(spec_dir)`, beside `kotlin_environment`

Returns an env naming `language: "java"` only. The toolchain comes from
`_LANG_ATTRS` via `generate_flake`, so this cannot drift from the provisioner —
the same property `kotlin_environment` has with the descriptor. `network` stays
restricted: Maven fetches from Central at run time, which the build-Job egress
policy already admits (measured on #1712).

### 3. `run_maven_lane_via_nix`, beside `run_gradle_lane_via_nix`

Same signature and contract: resolve the module root (`pom.xml`), dispatch the
Nix Job, parse the marker, read the merged report, return `None` when the
sandbox is unconfigured. A module-root resolver looks for `pom.xml` the way
`_gradle_module_dir` looks for `settings.gradle(.kts)`/`build.gradle(.kts)`.

### 4. Evaluator wiring

`_completed_java_subtasks` and `_resolve_java_runner_fn` mirror the Kotlin pair,
and `_build_all_bundles` gains a `java` branch. Kotlin's partition and runner are
untouched. `_stability_for_subtask`'s batching guard stays as #1311 left it:
only `jest` or python/unset batch through pytest, so Java cannot be sent there.

### 5. Framework descriptor `frameworks/maven/descriptor.yaml`

`name: maven`, `language: java`, `lanes: [unit]`, `source_extensions: [".java"]`,
`ac_command_tokens: ["mvn test", "mvn -B test", "./mvnw test"]`,
`test_path_conventions: ["src/test/java/**/*Test.java", ...]`, a JUnit 5
`context_block`, `coverage_strategy: skip` (the lane collects none), and a
nominal `runtime.image` with the `go-test` comment that the Nix substrate
supplies the toolchain.

The prompt vocabulary then derives `.java -> java` and the `mvn` tokens
automatically, because #1311 made that derivation descriptor-driven. No edit to
`prompts.py` is needed — which is the point of that change.

### 6. `frameworks/junit` stops claiming the unit lane

Its `lanes` become `[api]`. Its unit lane names a docker-host image that cannot
run here, and leaving two frameworks claiming `java.unit` would make the
Planner's choice ambiguous. `lang_registry`'s `java.unit` entry moves to the
new framework and flips `available_at_mvp` to `True`, because it now is.

**Risk accepted and checked:** any existing plan pinned to `(java, junit, unit)`
would become invalid at the post-emit validator. Step 1 of the plan greps for
such plans before the change lands.

## Alternatives rejected

- **Gradle-built Java** (the pre-revision plan): needs `gradle` in Java's attrs,
  which is a vendored hub canonical — a hub PR plus four re-vendors and pin
  bumps, to avoid one job script.
- **Widening `frameworks/junit` to cover both lanes.** Its runtime is the
  docker-host image; a single descriptor cannot honestly describe both an
  in-cluster Nix lane and that.
- **Parameterising the Kotlin lane by language.** Fewer functions, but it edits
  the one JVM lane currently proven in production to add an unproven one. A
  sibling function keeps Kotlin's path byte-identical.
- **Emitting `java` without a lane.** The #1311 failure shape: a plan that
  cannot run.

## Risks

- **Maven's first run downloads a lot.** Central is admitted by the egress
  policy, but the lane's timeout (Gradle's is 900s) may be tight for a cold
  local repository. The live run measures it; if it is marginal the timeout is
  raised deliberately, not silently.
- **Surefire's report path is assumed.** The merge glob must match what Maven
  actually writes; the live run confirms it rather than the docstring claiming
  it.
- **Two nearly identical job scripts.** Accepted for now: the duplication is
  visible and the alternative touches a proven lane. If a third JVM lane
  appears, factor then.
- **`junit`'s lane change touches existing Java planning.** Mitigated by the
  grep in plan step 1 and by the validator failing loudly rather than silently.
- **Kotlin regression.** Its tests, its partition and its runner must be
  untouched; the suite proves it.

## Verification

Deterministic:

1. `maven_job_script` contains `mvn -B test`, the `__MAVEN_EXIT` marker, a
   writable `-Dmaven.repo.local`, and the Surefire merge; every path quoted.
2. The merge snippet, run with bash over two sample `TEST-*.xml` files, yields
   one `<testsuites>` document.
3. `java_environment` names only the language; changing `_LANG_ATTRS` changes
   what is provisioned (no literals in `nix_env.py`).
4. `run_maven_lane_via_nix` returns `None` when `TFACTORY_NIX_RUNNER_IMAGE` is
   unset, and the evidence rule turns a zero exit with no report into 1.
5. Routing: a `language=java` unit subtask reaches the Maven runner and neither
   the pytest batch nor the Gradle runner. **Mutation:** point the Java
   partition at the Gradle runner and this test must fail.
6. The registry offers `maven: language=java, lanes=[unit]`, and
   `_validate_framework_consistency` accepts `(java, maven, unit)` while
   rejecting `(java, junit, unit)` and `(java, maven, browser)`.
7. The derived vocabulary gains `.java -> java` and the `mvn` tokens, and every
   other language's pin is byte-identical to today's — the #1311 guard.
8. Kotlin's full test set passes unchanged; each new module collected alone.

Live, in-cluster:

9. A Maven fixture (a `pom.xml` module with three passing JUnit 5 tests) staged
   under the `tfactory-data` mount, run through `run_maven_lane_via_nix`:
   returncode 0, `junit.xml` showing `tests="3" failures="0"`.
10. **Mutation:** break one assertion, re-run, require returncode 1 with the
    failing test named in the report.
11. A Java fixture driven through the **real Planner**, producing a
    `(java, maven, unit)` subtask at `src/test/java/...` that passes the
    post-emit validator unmodified — the #1311 standard.

Gates: ruff, ruff format over the CI path list, `ratchet_lint.py --base
origin/dev` with its `--package` flags, the hub security-sinks lint, and the
full suite.

---
status: draft
issue: 1311
author: olafkfreund
---

# Intent: the Planner can emit Kotlin subtasks

## Problem

TFactory 0.9.27 ships an in-cluster Kotlin/Gradle verify lane
(`run_gradle_lane_via_nix`, Factory#1712). The evaluator routes a subtask to it
when `language == "kotlin"`. Nothing ever produces such a subtask, so the lane
only runs when called directly from a test or by hand. It is dead weight on a
real task.

Measured on `origin/dev` at `12411d0e` (2026-09-23), the Planner cannot name
Kotlin at any point in the chain:

1. `apps/backend/prompts/planner.md:59` fixes the subtask schema to
   `"language": "<python|typescript|go|rust>"` and
   `"framework": "<pytest|jest|playwright|go-test>"`. Kotlin and Gradle are not
   options, and neither is the `junit` framework the registry already ships.
2. `prompts_pkg/prompts.py:539` `_EXT_LANGUAGE` has no `.kt` (nor `.kts`,
   `.java`). Run against a changed-file list, it returns the honest answer:
   `['app/src/main/kotlin/Calc.kt'] -> None`. This is the *strongest* signal the
   DETECTED PROJECT LANGUAGE block (#696) uses, so for a Kotlin build it is
   silent and weaker signals decide.
3. `_AC_COMMAND_LANGUAGE` maps `go test`, `cargo test`, `pytest`, `jest`,
   `vitest`. No `gradle test`, so an acceptance criterion written in Gradle's
   own terms pins nothing.
4. `frameworks/junit/descriptor.yaml` declares `language: java`. Rendering the
   FRAMEWORK REGISTRY block prints exactly one JVM row:
   `- junit: language=java, lanes=[unit, api], image=tfactory-runner-java:latest`.
   There is no Kotlin framework descriptor, and that image is the docker-host
   Java runner, not the in-cluster Nix Gradle lane.
5. `planner.md`'s test-generation guidance is Python/TypeScript/Go shaped
   (`_test.go` beside the code). Nothing states Kotlin's conventions:
   `src/test/kotlin/...` under the Gradle module, JUnit 5 / `kotlin.test`.

The likeliest failure today is not a clean refusal but a wrong answer: a Kotlin
repo has `build.gradle`, which the stack detector and the JVM manifest signals
read as **java**. The evaluator's Kotlin partition matches only the literal
string `kotlin`, so a `java` subtask misses the Gradle lane and lands nowhere.

Two things already know about Kotlin, which is what makes the gap a drift
rather than a missing feature: `project/stack_detector.py:89` appends `kotlin`
for `*.kt`, and `tools/runners/lang_registry.py` builds Kotlin's lane rows from
the vendored descriptor `languages/kotlin.yaml`, which marks the unit lane
`available: true`. The Planner prompt path simply does not consult them.

## Proposed outcome

A spec whose deliverable is Kotlin produces a plan whose unit subtasks carry
`language: "kotlin"`, name test files at Gradle's conventional path, and reach
`run_gradle_lane_via_nix` through the evaluator's existing partition, with
their results in the bundles and verdicts like any other lane.

Concretely, when this is done:

- A Kotlin changed-file set pins Kotlin, not `None` and not `java`.
- The Planner is told Kotlin is available, with the framework and lane the
  descriptor actually declares.
- One real planner-driven Kotlin task runs end to end, and its verdict is read
  from a real JUnit report, with a mutation proving the result is not
  pass-shaped.

## Affected users and systems

- TFactory Planner prompt path: `apps/backend/prompts/planner.md`,
  `prompts_pkg/prompts.py` (the registry, detected-language and
  framework-picking blocks).
- The framework registry under `frameworks/`, and its consumers.
- The evaluator's existing Kotlin partition (no change expected).
- Anyone running a JVM task: the same code decides Java's answer, so a change
  here can move Java's behaviour too.

## Constraints

- Must not regress Python, TypeScript, Go or Java planning. The
  language-pinning path is shared, and #443 and #696 exist because it was got
  wrong before.
- The evaluator matches the literal `kotlin`; the descriptor also declares the
  aliases `kotlin, android, kts`. Whatever is emitted must match what the
  evaluator partitions on, or the subtask is silently dropped again.
- A lane the descriptor marks unavailable must stay unavailable. Kotlin's
  browser and integration lanes are `available: false` and carry mandatory
  RFC-0006 VAL-0 reasons.
- Proof must be a real planner-driven run, not a hand-built plan. Factory#1712
  already proved the runner itself; this issue is about everything upstream of
  it.
- No new hardcoded language list if the same fact is already declared in a
  descriptor. The fleet rule is one engine, no drift.

## Open questions

1. **How far to fix the drift.** The measured cause is that four hardcoded
   lists in the prompt path duplicate what descriptors already declare. Options:
   (a) add Kotlin to each list, smallest diff, drift stays and the next
   language repeats this issue; (b) derive the prompt's language and framework
   vocabulary from the descriptors and registry, so onboarding a language stays
   a descriptor drop, as `lang_registry` already promises. My recommendation is
   (b) scoped to the Planner prompt path only, with (a)'s lists deleted as they
   are replaced, but it is the larger change and it moves Java's behaviour too.
2. **Kotlin's framework identity.** Reuse `junit` by widening it to the JVM, or
   add a `gradle` framework descriptor pointing at the Nix lane? The existing
   `junit` row names a docker-host image the in-cluster lane does not use.
3. **Java.** `.java` also maps to nothing today, and `junit`'s lane is the
   docker-host runner. Do we fix Java in the same change, or record it as a
   separate issue and keep this one Kotlin-only?
4. **What counts as the end-to-end proof.** A fixture Kotlin repo driven through
   the real Planner in-cluster, or a full PARR run from a spec? The former is
   reproducible in CI; the latter is the honest user-level claim.

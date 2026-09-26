---
status: draft
issue: 1321
author: olafkfreund
---

# Intent: a Java deliverable gets tests that actually run

## Problem

Java is where Kotlin was before #1311, with one extra gap: even if the Planner
could name it, there is nowhere in-cluster for the tests to run.

Measured on `origin/dev` at 0.9.28 (2026-09-26):

1. **The Planner cannot pin Java.** `_language_from_files(['src/main/java/Calc.java'])`
   returns `None`. The derived extension map is
   `{.go, .js, .jsx, .kt, .kts, .py, .rs, .ts, .tsx}` — `.java` is absent,
   because #1311 deliberately left it out (no lane to prove a fix against).
2. **No acceptance-criteria token names Java.** The AC map has `gradle test` and
   `./gradlew test` (added for Kotlin) but no `mvn test`. A Java spec written in
   Maven's terms pins nothing.
3. **The only JVM framework targets the docker-host substrate.**
   `frameworks/junit/descriptor.yaml` declares `language: java`,
   `runtime.image: tfactory-runner-java:latest`, `entrypoint: mvn -B test`. The
   cluster has no container runtime, so that lane cannot run in-cluster at all.
4. **Java has no language descriptor.** `contracts/languages/` holds only
   `kotlin.yaml` and `swift.yaml`, so no nix toolchain is declared for Java
   anywhere — which is how the in-cluster lanes get their compilers.

So a Java deliverable today is unpinned at best. If we only fixed the
vocabulary, we would be planning subtasks that route to a lane the cluster
cannot execute — the same "plan something that cannot run" shape #1311 fixed,
moved one layer along.

**What already works in our favour:** the Gradle lane built for #1311 is not
Kotlin-specific in any deep way. `gradle_job_script` runs `gradle test
--no-daemon`, merges the per-class JUnit XML, and treats a zero exit with no
report as a failure — all language-neutral. The nix environment it uses carries
`kotlin, gradle, jdk21`, and Gradle builds Java with that same JDK. The
Kotlin-specific parts are the environment's `language: "kotlin"` label
(`nix_env.py:839,843`) and the evaluator's partition on that literal string.

## Proposed outcome

A spec whose deliverable is Java produces unit subtasks that are pinned as Java,
carry a framework whose lane exists, and run to a real verdict in-cluster —
proven by a live run with real test counts and a mutation that flips it.

## Affected users and systems

- TFactory's Planner prompt path (the shared language-pinning code #443/#696
  guard, and #1311 made descriptor-derived).
- The evaluator's lane partitions and the Nix Gradle lane.
- The hub: a new `contracts/languages/java.yaml` would need vendoring into all
  four services, with the pin bump and drift gate that implies.
- `frameworks/junit` and any Java planning that exists today.
- Not Kotlin: its lane must keep behaving exactly as it does now.

## Constraints

- **Do not plan what cannot run.** Vocabulary and lane land together, or not at
  all.
- **Do not regress Kotlin**, or Python/TypeScript/Go/Rust. The pinning path is
  shared; #1311 proved non-regression by rendering the prompt per language and
  requires the same here.
- **A lane is proven only by a real in-cluster run** with real counts plus a
  mutated failing test. That is the standard #1712 and #1311 set.
- **One engine, no drift.** A Java toolchain belongs in a hub language
  descriptor, not as literals in TFactory.
- The evaluator partitions on a literal language string; whatever is emitted
  must match exactly, or subtasks are silently dropped again.

## Open questions

1. **Gradle first, or Maven first?** Reusing the proven Gradle lane for
   Gradle-built Java is much the cheaper path and needs no new job script. But
   `frameworks/junit`'s manifest signals lead with `pom.xml`, so real-world Java
   here may be Maven-shaped. My recommendation is Gradle-built Java first,
   because it reuses a lane already proven live, with Maven as a follow-up —
   but if the projects we actually verify are Maven, that ordering is wrong.
2. **Reuse `frameworks/junit` or add a Gradle-Java framework?** Widening
   `junit` changes what existing Java planning is told and its runtime still
   names the docker-host image. A separate descriptor (as Kotlin got) keeps the
   change additive. Either way one of them must stop claiming a lane it cannot
   run.
3. **How far to generalise the lane?** Options: parameterise the existing
   Kotlin lane by language; or add a thin `java_environment` beside
   `kotlin_environment` sharing the same job script. The second is a smaller
   diff and keeps Kotlin's path untouched; the first avoids two nearly identical
   functions.
4. **Does `frameworks/junit` keep claiming the unit lane?** If Java's in-cluster
   route is Gradle, junit's docker-host unit lane is unrunnable here and should
   probably be marked unavailable with a reason, the way
   `languages/kotlin.yaml` marks its browser lane.

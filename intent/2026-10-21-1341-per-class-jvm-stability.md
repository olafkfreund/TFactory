---
status: draft
issue: 1341
author: olafkfreund
---

# Intent: JVM verdicts come from each test's own results, per build root

Covers #1341 and #1356.

## Problem

The Kotlin and Java lanes run the whole build (`gradle test` / `mvn test`) and
compute one 3x stability result from the first subtask (`kotlin[0]`,
`java[0]` in `apps/backend/agents/evaluator.py` `_build_all_bundles`), then
give that one result to every subtask. Two consequences:

- #1341: the result is the build's exit code, so two real failures in one
  class mark all 16 generated tests `consistent_fail` and the run ends
  `triaged_empty`, discarding 14 good tests. The merged per-class JUnit report
  is already staged by the lane but only its totals are read.
- #1356: the module is resolved from the first subtask's path only
  (`_maven_module_dir` / `_gradle_module_dir`), so in a multi-module repo tests
  staged into other modules never run but still receive the first module's
  result.

## Proposed outcome

- Each Java/Kotlin subtask's stability is derived from its own test class's
  `<testcase>` results in the merged JUnit report of each run.
- A class that is absent from a report that exists is reported as not run
  (`error`), never as `consistent_fail`.
- Subtasks are grouped by resolved build root; one 3x pass runs per group.
- Pinned by a test: two failures in class A leave class B `stable`.

## Affected users and systems

TFactory evaluator Java/Kotlin lanes (`apps/backend/agents/evaluator.py`).
No change to the Nix Job scripts, the Go lane, or the Python/Jest lanes.

## Constraints

- No new Nix Jobs per subtask: still one 3x pass per build root.
- No `xml.etree` (security gate S314); read the report with regexes like
  `_junit_counts`.
- When a run has no report at all (compile failure), keep today's build-level
  result for that run.

## Open questions

None. Go has the same shared-stability shape but no per-file class mapping
in gotestsum's report; it is left out of this change.

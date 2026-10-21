---
status: approved
issue: 1341
intent: intent/2026-10-21-1341-per-class-jvm-stability.md
---

# Spec: JVM verdicts come from each test's own results, per build root

Covers #1341 and #1356.

## Design

All in `apps/backend/agents/evaluator.py`.

1. New `_jvm_bundles(spec_dir, project_dir, subtasks, runner_fn, root_of)`
   replaces the Kotlin and Java blocks of `_build_all_bundles`:
   - Group subtasks by `root_of(st)` (insertion order kept).
     Kotlin: `_gradle_module_dir(project_dir, Path(files_to_create[0]))`.
     Java: `_maven_module_dir` when `_java_build_tool(project_dir) == "maven"`,
     else `_gradle_module_dir`. These are the same resolvers the lanes use, fed
     the same hint the runner passes, so a group maps to exactly one build.
   - Per group: wrap `runner_fn` so each call also records the text of
     `res.junit_xml_path` (or None) right after the run (the next run wipes the
     stage dir). Run `_stability_for_subtask` once with the group's first
     subtask and the wrapper.
   - Per subtask: `_per_class_stability(shared, reports, st)` and pass it to
     `_build_go_signal_bundle(..., stability=...)`.
2. `_per_class_stability`: returns `shared` unchanged when it is None, ERROR,
   or the report count does not match the run count. Otherwise for each run:
   - no report → keep the build-level run (today's behaviour, e.g. compile
     failure);
   - report present, class has no `<testcase>` → return
     `StabilityResult(ERROR, error_message="<Class> has no results in the
     JUnit report: this test did not run")`;
   - else returncode 0 if none of the class's testcases has `<failure` or
     `<error`, else 1 with `"FAILED <Class>: this test failed"` prefixed to the
     stdout tail (keeps `failure_kind` = assertion).
   Verdict from the shared `classify_stability_runs`.
3. `_class_outcome(report, stem) -> bool | None`: regex over `<testcase>`
   elements (quoted attributes may contain `>`), matching `classname` equal to
   the file stem, ending in `.<stem>`, or a nested `<stem>$...` class.

## Alternatives rejected

- Run each test class alone (`--tests`, `-Dtest=`): 3 Nix Jobs per subtask,
  the cost the shared pass was introduced to avoid.
- Run the reactor root for every module (#1356 alt): breaks per-module
  `pom.xml` builds that are not aggregated, and still needs per-class reading.
- Parse with `xml.etree`: rejected by the S314 security gate.
- Go: gotestsum reports by package, and a test file maps to function names,
  not a class; out of scope here.

## Risks

- A Kotlin file whose class name differs from its file name now reports
  `error` (not run) instead of the build result. Convention is file = class;
  `error` is honest and flags rather than rejects.
- Multi-module repos now run one 3x pass per module: more Jobs, by design.

## Verification

- New tests in `tests/test_evaluator_kotlin_lane.py` /
  `tests/test_evaluator_java_lane.py`: a report with 2 failures in class A
  leaves class B `stable` and A `consistent_fail`; a missing class is `error`;
  two Maven modules give two groups, each run with its own hint.
- The new tests fail on origin/dev; full evaluator test files and ruff pass.

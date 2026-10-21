---
status: draft
issue: 1341
spec: spec/2026-10-21-1341-per-class-jvm-stability.md
---

# Plan: JVM verdicts come from each test's own results, per build root

Covers #1341 and #1356.

## Approved decisions (self-contained)

- Java/Kotlin subtasks are grouped by resolved build root; one 3x stability
  pass runs per group (the group's first subtask is the runner hint).
  Kotlin root: `nix_env._gradle_module_dir(project_dir, Path(files_to_create[0]))`.
  Java root: `nix_env._maven_module_dir` when `_java_build_tool(project_dir) ==
  "maven"`, else `_gradle_module_dir`.
- A wrapper around the group's runner_fn records each run's JUnit report text
  (`res.junit_xml_path`, read immediately; None when absent or unreadable).
- Each subtask's stability is rebuilt from its own class's `<testcase>`s:
  - shared result None / ERROR / report count != run count → shared unchanged;
  - run without report → that run's build-level record unchanged;
  - report present, class absent → `StabilityResult(verdict=ERROR, runs=shared.runs,
    seed, rerun_count, error_message="<Stem> has no results in the JUnit
    report: this test did not run")`;
  - class present → `StabilityRun(returncode=0)` if no `<failure`/`<error` in
    its testcases, else `returncode=1` with stdout_tail
    `"FAILED <Stem>: this test failed\n" + run.stdout_tail`;
  - verdict via `classify_stability_runs`.
- Class match: testcase `classname` == stem, endswith `"." + stem`, or its last
  dotted segment starts with `stem + "$"`. Stem = `Path(files_to_create[0]).stem`.
- Regex only (no `xml.etree`, gate S314). Go lane unchanged.

## Steps

1. `apps/backend/agents/evaluator.py`, after `_build_go_signal_bundle`
   (~line 2365): add `_class_outcome(report: str, stem: str) -> bool | None`,
   `_per_class_stability(shared, reports, subtask)`, and
   `_jvm_bundles(spec_dir, project_dir, subtasks, runner_fn, root_of) ->
   list[EvaluatorSignals]` per the decisions. Testcase regex must tolerate `>`
   inside quoted attributes and self-closing `<testcase .../>`.
   → verify by `ruff check apps/backend/agents/evaluator.py`
   Traps: lazy-import `agents.nix_env` resolvers inside the function (tests
   patch nix_env module attributes); keep `_stage_go_test` staging before
   resolving roots (roots rglob the project dir).
2. `apps/backend/agents/evaluator.py` `_build_all_bundles` kotlin/java blocks
   (~2699-2741): keep staging and runner resolution, replace the shared
   stability + list comprehension with `bundles += _jvm_bundles(...)` and the
   root_of lambdas above. → verify by
   `pytest tests/test_evaluator_kotlin_lane.py tests/test_evaluator_java_lane.py`
   Traps: `test_java_stability_is_computed_once_for_the_module` must still see
   exactly 3 runs for 3 subtasks in one module.
3. Tests in `tests/test_evaluator_java_lane.py` and
   `tests/test_evaluator_kotlin_lane.py`:
   - fake lane returns returncode=1 and a junit.xml where `ATest` has 2
     failing testcases and `BTest` passes → B `stable`, A `consistent_fail`;
   - a class absent from the report → `error`;
   - Java: two modules `a/pom.xml`, `b/pom.xml` with one subtask each → 6 runs,
     hints from both modules.
   → verify they fail on origin/dev's evaluator.py and pass after.

## Tests

`cd apps/backend && pytest ../../tests/test_evaluator_kotlin_lane.py
../../tests/test_evaluator_java_lane.py ../../tests/test_evaluator.py -q`
(or the repo's configured invocation) all pass; `ruff check` and
`ruff format --check` clean on touched files.

## Rollback

Revert the PR; no data or schema changes.

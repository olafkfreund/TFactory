---
status: approved
issue: 1637
intent: intent/2026-11-19-1637-tf-preflight-ambiguous-root.md
---

# Spec: Retry the probe against every root of an ambiguous package

## Design

In `check_import`, factor the subprocess run into `_probe(order)`. If the first probe exits 2 (missing attribute) and `package_roots_for` returned more than one root, re-run with each other root first and accept the first copy that provides the name (a broken sibling is skipped). Reject only if every copy lacks the name. Exit 2 with one root is unchanged, so the `_shadowed_elsewhere` logic and third-party behaviour stay as is.

## Alternatives rejected

- Prefer the root containing the subtask's target file: needs plumbing through `preflight_check` and the target may be a test path, not the SUT. Trying all roots needs no new input.
- Execution time (`package_root_rel_paths`, evaluator, `nix_env._in_job_pythonpath`): ordering is arbitrary without a target, and the api lane serves via `--app-dir` (#1582, #1633), so HTTP tests do not import `app`. Out of scope; reported in the PR.

## Risks

A name present only in the wrong copy is accepted; the real run then fails at collection, which is the correct place. Up to N extra probes (15 s cap each) only on exit 2 with several roots.

## Verification

New tests in `tests/test_preflight_static.py` using the two-`app` layout: name in the second copy is accepted (fails without the fix); name in neither is still rejected.

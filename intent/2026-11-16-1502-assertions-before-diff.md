---
status: draft
issue: 1502
author: olafkfreund
---

# Intent: the test generator writes assertions before it reads the diff

## Problem

`apps/backend/prompts/gen_functional.md` tells the generator never to take an
expected value from the implementation (rule 5), then tells it to read
`context/diff.patch` (workflow step 2) and the target source (step 3) before it
writes the test (step 6). The rule works only if the model ignores what it has
just read. A test written after reading the code tends to confirm the code,
which is the failure #888 was about. #888 added a rule and a literal check; the
order of work is unchanged.

## Proposed outcome

- Generation runs in two steps. Step 1 writes the assertions from the subtask's
  criterion only, with no diff and no target source in its instructions.
- Step 2 gets the diff and the target, for wiring only (imports, signatures,
  fixtures). The expected values from step 1 are fixed.
- A check rejects the file if an expected value from step 1 is missing in the
  final file (after any import-repair retry); the existing rejection path
  (delete, replan request) is used.
- A regression test: with a criterion that contradicts the implementation, the
  step-1 file asserts the criterion, and a step 2 that swaps in the
  implementation's value is rejected; the kept assertion fails against the
  implementation, so the contradiction reaches a human.

## Affected users and systems

TFactory Gen-Functional (`agents/gen_functional.py`, `prompts_pkg/prompts.py`,
`prompts/gen_functional*.md`). Cost per generated test rises by one session.

## Constraints

- Must not weaken the #888 and #995 checks.
- Must keep the legacy (framework-less) path working unchanged.
- The SDK client grants Read/Glob/Grep per session and `diff.patch` lives in
  `spec_dir`, so step 1 cannot be technically isolated from the source. Step 1
  is enforced by its instructions; step 2 by a hard check on the output. This
  is a reduced guarantee against the issue text and the owner approves it with
  the intent. The check is presence-based: a value kept as unused data while a
  different one is asserted is caught by the #888 criterion check, not here.
- Must be switchable off with an env var if cost proves too high.

## Open questions

- The issue requires the cost and duration change measured on sample runs. That
  needs live runs the lead cannot start; the PR reports it as an open blocker
  and states the expected change (one extra session per subtask).

---
status: approved
issue: 1428
intent: intent/2026-10-24-1428-codeql-barriers.md
---

# Spec: Clear the two re-opening CodeQL injection alerts at the call site, and lower the ratchet baseline

## Design

Paths are under `apps/web-server/server/routes/` unless stated.

### D1. Worktree `name` goes through `safe_component` (alert 1893)

In `git.py` worktree create, keep the existing `re.fullmatch(r"[a-zA-Z0-9_-]+", name)`
check and its friendly error, then add `name = safe_component(name)`, imported
from `._specpath` next to `safe_join`. `safe_component` is already a
path-injection barrier in `PathInjectionSanitized.ql` and returns the value, so
`worktree_path` and the `shutil.rmtree` cleanup are no longer tainted. Any name
that passes the fullmatch is a bare component, so behaviour does not change.
This is a call-site change only.

### D2. `/diff` `base` uses the changelog ref allow-list (alert 1908)

In `files.py` `get_git_diff`, replace the leading-`-` check with
`base = _require_safe_git_ref(base, "base ref")`, imported from `.changelog`.
That is the same function and regex `changelog.py` uses: `[\w./@-]+`, with a
leading `-` and anything else out of charset refused with HTTP 400.

A call-site change cannot clear this alert on its own. The command-injection
twin's only barrier is `_safe_arg`, a path charset that lives in
`worktree_tools`, and stock CodeQL does not treat a regex fullmatch as a
barrier. So `CommandInjectionSanitized.ql` adds `_require_safe_git_ref` to its
barrier list (the call result, plus the function's first parameter so the
checks inside the helper do not re-fire). That covers both copies, in `git.py`
and `changelog.py`, because the query matches by name. It is honest because:
the function returns the value only after refusing a leading `-` and any
character outside `[\w./@+-]`, so the value holds no shell metacharacter and
cannot be read as an option; and every caller (`git.py` worktree base branch
and release version, `changelog.py` from/to tags, and now `/diff`) passes it
as one element of a list argv with no `shell=True`. The `.ql` comment says
this.

### D3. Baseline to today's open counts

`.github/codeql-baseline.json` becomes `{"critical": 0, "high": 0, "error": 0, "medium": 0}`.
These are the counts in `gh api 'repos/olafkfreund/TFactory/code-scanning/alerts?state=open'`,
bucketed as the hub `scripts/codeql_ratchet.py` does (security severity, else
rule severity). Alert 1910 (`actions/unpinned-tag`, the `codeql-ratchet.yml`
caller on the hub's `@main`) is dismissed, so it does not count.

## Alternatives rejected

- **Add `_require_safe_git_date` and `_validate_ref` to the barriers as well.**
  They would let 1906 and 1909 stay closed after a refactor, but both are
  dismissed already and the lead scoped this lane to the two call sites. Each
  one is a single list entry if it is needed later.
- **Use `_safe_arg` for `base`.** It is a path charset, not a ref allow-list,
  and importing it from `worktree_tools` into `files.py` couples unrelated routes.
- **Keep the leading-dash check and only barrier it.** That check is weaker
  than an allow-list, and a barrier on it would be dishonest.
- **Pin 1910's `@main` to a SHA.** That is a hub-wide caller convention and
  out of scope; 1910 is dismissed.

## Risks

- `/files/{id}/diff` now refuses `~` and `^` in `base` (e.g. `HEAD~1`). No UI or
  test caller sends this route today (the UI uses `/tasks/{id}/worktree/diff`).
  The default `HEAD` still passes.
- The barrier matches any function named `_require_safe_git_ref`. A future
  copy with a weaker body would be trusted as well. The `.ql` comment names
  the contract.
- Importing from `.changelog` into `files.py`: `changelog` imports only
  `_specpath`, `error_ref` and `insights_service`, so there is no cycle.

## Verification

- New tests in `apps/web-server/tests/`: `/diff` refuses `-x`, `a;b` and
  `HEAD~1` with 400 and accepts `HEAD`; worktree create still refuses a bad
  name and builds `worktree_path` from the validated name.
- The existing web-server tests for git, files and changelog pass.
- CodeQL on the PR reports no new `py/path-injection-sanitized` or
  `py/command-line-injection-sanitized` alert on `git.py` or `files.py`.

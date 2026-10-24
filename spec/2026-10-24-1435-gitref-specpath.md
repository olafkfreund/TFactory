---
status: approved
issue: 1435
intent: intent/2026-10-24-1435-gitref-specpath.md
---

# Spec: Move the git ref and date validators into _specpath to break the import cycle

## Design

All paths are under `apps/web-server/server/routes/`.

- **D1** Move `_GIT_REF_RE`, `_GIT_DATE_RE`, `_require_safe_git_ref` and
  `_require_safe_git_date` into `_specpath.py`. It already imports `re`
  and `HTTPException` and imports nothing from routes, so it is a leaf.
- **D2** The shared ref regex is `git.py`'s `[\w./@+-]+`, a superset of
  changelog's `[\w./@-]+`. `git.py` needs `+` for release versions such as
  `1.0.0+build`. A `+` in a single argv element with no shell cannot inject
  anything, so allowing it in changelog tags and the `/diff` base is safe.
  The leading-`-` refusal stays. The signature is `git.py`'s,
  `(value: str, label: str = "git ref") -> str`; every changelog caller
  already passes a label.
- **D3** `changelog.py` and `git.py` delete their copies and import from
  `._specpath` (extending the existing `from ._specpath import ...` line).
  `files.py` swaps `from .changelog import _require_safe_git_ref` for the
  `._specpath` import.
- **D4** Update the paths in the `CommandInjectionSanitized.ql` comment
  (`routes/_specpath.py`). The query logic is unchanged.

## Alternatives rejected

- **Keep both regexes.** That needs two functions or a flag, so the drift stays.
- **Use changelog's stricter regex.** It would make `git.py` reject release
  versions with `+`.
- **A new `_gitref.py` module.** `_specpath` is already the shared home for
  request validators that CodeQL treats as barriers.

## Risks

- Changelog tags and the `/diff` base now accept `+`. Harmless, per D2.
- `re` may become unused in `changelog.py` or `git.py`. Ruff will catch it.

## Verification

- The existing web-server tests pass unchanged, including
  `tests/test_codeql_1428_barriers.py`.
- One new test in `tests/test_specpath.py`: the shared validator accepts
  `v1.0.0+build` and `HEAD`, and rejects `-x`, `a;b` and `HEAD~1` with 400.
  `_require_safe_git_date` accepts `2026-10-24` and rejects `-1`.
- `grep -rn "def _require_safe_git" apps/web-server` shows only `_specpath.py`.
- The CodeQL run on the PR has no `py/cyclic-import` at `files.py`, and no
  new `-sanitized` alerts.

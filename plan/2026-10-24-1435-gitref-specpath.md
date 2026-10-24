---
status: approved
issue: 1435
spec: spec/2026-10-24-1435-gitref-specpath.md
---

# Plan: Move the git ref and date validators into _specpath to break the import cycle

Approved decisions (from the spec):

- **D1** `_GIT_REF_RE`, `_GIT_DATE_RE`, `_require_safe_git_ref` and
  `_require_safe_git_date` live only in `routes/_specpath.py`, a leaf module.
- **D2** The ref regex is `git.py`'s `[\w./@+-]+`. The signature is
  `_require_safe_git_ref(value: str, label: str = "git ref") -> str` with the
  leading-`-` refusal and HTTP 400 `f"Invalid {label}"`. The date validator is
  changelog's, unchanged (`[\w.:+/\s-]+`, required `label`).
- **D3** `changelog.py`, `git.py` and `files.py` import both names (only the
  ones each uses) from `._specpath`. Their own copies are deleted.
- **D4** The `.ql` doc comment names `routes/_specpath.py`. Logic unchanged.

All paths are under `apps/web-server/` unless they start with `.github/`.

## Steps

1. `server/routes/_specpath.py`: append the two compiled regexes (with
   `git.py`'s allow-list comment, :33-38) and both functions, using `git.py`'s
   ref docstring and changelog's date function body.
   → verify: `../backend/.venv/bin/python -c "from server.routes._specpath import _require_safe_git_ref, _require_safe_git_date"`
   Traps: `_specpath` must keep importing nothing from `server.routes`.
2. `server/routes/changelog.py`:26-58: delete the comment, both regexes and
   both functions. Line 20 becomes
   `from ._specpath import _require_safe_git_date, _require_safe_git_ref, safe_component`.
   Keep `import re` (still used at :85).
   → verify: `python -c "import server.routes.changelog"`
3. `server/routes/git.py`:33-52: delete the comment, `_GIT_REF_RE` and the
   function. Line 24 becomes
   `from ._specpath import _require_safe_git_ref, safe_component, safe_join`.
   Drop `import re` only if ruff says it is unused.
   → verify: `python -c "import server.routes.git"`
4. `server/routes/files.py`:28-29: delete `from .changelog import _require_safe_git_ref`
   and add `_require_safe_git_ref` to the `._specpath` import.
   → verify: `python -c "import server.routes.files"`
5. `.github/codeql/custom-queries/CommandInjectionSanitized.ql`: in the comment,
   `(routes/changelog.py, routes/git.py)` → `(routes/_specpath.py)`.
   → verify: `grep -n "_specpath" .github/codeql/custom-queries/CommandInjectionSanitized.ql`
6. `tests/test_specpath.py`: add `_require_safe_git_ref, _require_safe_git_date`
   to the existing `from server.routes._specpath import (...)`, then add
   `test_git_ref_allow_list` (accepts `HEAD` and `v1.0.0+build`; rejects
   `-x`, `a;b` and `HEAD~1` with `HTTPException` status 400) and
   `test_git_date_allow_list` (accepts `2026-10-24`; rejects `-1` with 400).
   → verify: the Tests command below.

Run `../backend/.venv/bin/ruff check` and `ruff format` on the changed .py files.
Run all commands from `apps/web-server`.

## Tests

- `cd apps/web-server && ../backend/.venv/bin/python -m pytest tests/test_specpath.py tests/test_codeql_1428_barriers.py tests/test_files_discover_roots.py -q` → all pass, with no existing test edited.
- `grep -rn "def _require_safe_git" apps/web-server/server` → only `_specpath.py`.
- Commit with hooks on.

## Rollback

Revert the PR.

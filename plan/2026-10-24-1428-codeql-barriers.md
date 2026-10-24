---
status: approved
issue: 1428
spec: spec/2026-10-24-1428-codeql-barriers.md
---

# Plan: Clear the two re-opening CodeQL injection alerts at the call site, and lower the ratchet baseline

Approved decisions, copied from the spec:

- **D1** `routes/git.py` worktree create: after the existing `re.fullmatch`
  name check, add `name = safe_component(name)` (barrier in
  `PathInjectionSanitized.ql`). Clears 1893 (`shutil.rmtree(worktree_path)`).
  Call-site only.
- **D2** `routes/files.py` `get_git_diff`: replace the leading-`-` check with
  `base = _require_safe_git_ref(base, "base ref")` from `.changelog`
  (regex `[\w./@-]+`, leading `-` refused, HTTP 400). Add `_require_safe_git_ref`
  to the barrier list in `CommandInjectionSanitized.ql` (call result + first
  parameter), with a comment on why it is honest: it returns only a value with
  no shell metacharacter and no leading `-`, and every caller passes it as one
  element of a list argv with no shell. Clears 1908.
- **D3** `.github/codeql-baseline.json` = `{"critical": 0, "high": 0, "error": 0, "medium": 1}`.
- Out of scope: barriers for `_require_safe_git_date` / `_validate_ref`, pinning
  the ratchet caller's `@main`, dismissing anything.

All paths below are under `apps/web-server/` unless they start with `.github/`.

## Steps

1. `server/routes/git.py`:24 and :1230-1235: change the import to
   `from ._specpath import safe_component, safe_join`; directly after the
   `if not re.fullmatch(r"[a-zA-Z0-9_-]+", name): return {...}` block add
   `name = safe_component(name)` with a one-line comment saying it is the
   path-injection barrier CodeQL recognises.
   → verify: `cd apps/web-server && ../backend/.venv/bin/python -c "import server.routes.git"`
   Traps: keep the friendly `{"success": False, ...}` error for a bad name; it
   runs first, so `safe_component`'s 400 never fires for a name that passed it.

2. `server/routes/files.py`:28 and :837-846: add
   `from .changelog import _require_safe_git_ref`; replace the
   `if base.startswith("-"): raise HTTPException(...)` block with
   `base = _require_safe_git_ref(base, "base ref")`, and update the comment above
   it to say the allow-list replaces the dash check. Keep the `--` before `path`.
   → verify: `python -c "import server.routes.files"` (same venv/cwd as step 1)
   Traps: import from `.changelog`, not `.git` (the `git.py` regex also allows `+`;
   the spec chose changelog's). Do not import at function level.

3. `.github/codeql/custom-queries/CommandInjectionSanitized.ql`: in both
   `name in ["_safe_arg"]` and `f.getName() in ["_safe_arg"]`, add
   `"_require_safe_git_ref"`. Extend the doc comment with one paragraph:
   `_require_safe_git_ref` (`routes/changelog.py`, `routes/git.py`) returns the
   value only after refusing a leading `-` and any character outside
   `[\w./@+-]`; every caller passes it as one element of a list argv with no
   shell, so it cannot pick the program or inject an option (#1428).
   → verify: `grep -c _require_safe_git_ref .github/codeql/custom-queries/CommandInjectionSanitized.ql` ≥ 3
   Traps: there is no local CodeQL CLI; the PR's CodeQL check is the real test.

4. `.github/codeql-baseline.json`: set to
   `{"critical": 0, "high": 0, "error": 0, "medium": 1}` with the same key order
   and 2-space indent as now.
   → verify: `python3 -m json.tool .github/codeql-baseline.json`
   Traps: none.

5. Tests, new file `tests/test_codeql_1428_barriers.py`:
   - `/diff`: build an app like `tests/test_files_discover_roots.py` does
     (`FastAPI()` + `files.router`), monkeypatch `files.resolve_path` to return a
     `tmp_path` repo made with `git init` + one commit. Assert
     `GET /{id}/diff?base=-x`, `base=a;b`, `base=HEAD~1` → 400, and `base=HEAD` → 200.
   - worktree name: assert `safe_component` is called on the name, by calling
     `create_worktree` with a projects file from a monkeypatched
     `server.config.get_settings` (an object whose `projects_file` points at a
     tmp JSON `{"projects": [{"id": "p", "path": <tmp git repo>}]}`); a bad name
     (`../x`) returns `success: False`, and a good name (`ok-1`) creates
     `<repo>/.tfactory/worktrees/tasks/ok-1`.
   → verify: `cd apps/web-server && ../backend/.venv/bin/python -m pytest tests/test_codeql_1428_barriers.py -q`
   Traps: worktrees need a git user in the tmp repo (`-c user.email=t@t -c user.name=t`);
   tests run from `apps/web-server`.

## Tests

- `cd apps/web-server && ../backend/.venv/bin/python -m pytest tests/test_codeql_1428_barriers.py tests/test_files_discover_roots.py tests/test_specpath.py -q` → all pass.
- Commit with hooks on (pre-commit runs ruff and the critical lane).
- On the PR: CodeQL shows no new `-sanitized` alert on `git.py` or `files.py`.

## Rollback

Revert the PR. The baseline revert restores `critical: 4, high: 1`.

---
status: approved
issue: 1257
spec: spec/2026-09-18-1257-codeql-alembic-false-positives.md
---

# Plan: Stop Alembic migrations raising permanent CodeQL false positives

Approved decisions (self-contained):

- Fix at the source: every Alembic migration declares a literal `__all__`
  listing `revision`, `down_revision`, `branch_labels`, `depends_on`,
  `upgrade`, `downgrade`. CodeQL `py/unused-global-variable` skips module
  exports (`getAnExport()`), so the four alerts per file disappear.
- `__all__` must be a **literal list of string literals** (a computed one
  trips `complex_all` — silences the rule for the wrong reason).
- **No** change to `.github/codeql/codeql-config.yml`: no `query-filters`, no
  `paths-ignore`, no SARIF filter, no inline suppression.
- Scope: the template + the 10 existing migrations in TFactory. AIFactory not
  touched.
- The 40 existing alerts close by re-scan, not by hand dismissal.

Branch: `fix/1257-codeql-alembic-false-positives` (off `origin/dev`).
Paths below are relative to `apps/web-server/`.

## Steps

1. `server/database/alembic/script.py.mako`: after the `depends_on` line, add

   ```python
   # Alembic reads these via its script loader, never via Python references;
   # listing them as exports tells CodeQL py/unused-global-variable so (#1257).
   __all__ = [
       "revision",
       "down_revision",
       "branch_labels",
       "depends_on",
       "upgrade",
       "downgrade",
   ]
   ```

   → verify by step 3's probe revision containing the block verbatim.
2. `server/database/alembic/versions/*.py` (10 files): insert the same block
   after each file's `depends_on` assignment. Addition only — no revision id,
   `down_revision` or body changes.
   → verify by `git diff --stat` showing 10 files, insertions only, and
   `grep -L "__all__" server/database/alembic/versions/*.py` printing nothing.
3. Template check: `alembic revision -m probe` in a scratch copy, confirm the
   generated file has the block, delete the probe.
   → verify by `grep -c '"depends_on",' <probe>` = 1; probe file removed,
   `git status` clean of it.
4. Migration graph unchanged: run the chain up and down on a scratch SQLite
   DB (`DATABASE_URL` pointed at a temp file; `env.py:37` reads it).
   → verify by `alembic upgrade head` then `alembic downgrade base` exiting 0,
   and `alembic heads` showing the same single head as on `dev`.
5. Lint/format: `ruff check` and `ruff format --check` on the touched files.
   → verify by both exiting 0 (the format gate now covers `apps/web-server`).
6. Commit (`fix(codeql): declare Alembic identifiers as exports (#1257)`),
   push branch, open PR to `dev` linking intent/spec/plan.
   → verify by PR CodeQL run reporting zero `py/unused-global-variable`
   alerts on `alembic/versions/*`.
7. After merge to `dev`: code scanning re-scan.
   → verify by `gh api repos/olafkfreund/TFactory/code-scanning/alerts?state=open`
   filtered to rule `py/unused-global-variable` + path `alembic/versions/`
   returning 0; then close #1257.

## Tests

```bash
cd apps/web-server
grep -L "__all__" server/database/alembic/versions/*.py          # expect: no output
export DATABASE_URL=sqlite+aiosqlite:///$(mktemp -d)/t.db   # env.py:37 reads it
../backend/.venv/bin/alembic upgrade head
../backend/.venv/bin/alembic downgrade base
ruff check server/database/alembic && ruff format --check server/database/alembic
```

Rule still live elsewhere (mutation check): on a throwaway branch, add a module
with an unused global; the PR's CodeQL run must flag it. Branch deleted after.

## Rollback

Revert the single commit. `__all__` has no runtime effect on Alembic (it only
affects `from x import *`, which Alembic never does), so reverting restores
the previous alerts and nothing else.

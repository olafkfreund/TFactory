---
status: approved
issue: 1257
intent: intent/2026-09-18-1257-codeql-alembic-false-positives.md
---

# Spec: Stop Alembic migrations raising permanent CodeQL false positives

## Design

Declare the Alembic identifiers as module exports, so the rule no longer
considers them unused. No CodeQL config change, no suppression.

`py/unused-global-variable` (`python/ql/src/Variables/UnusedModuleVariable.ql`
in github/codeql) excludes any variable where

```
unused.getEnclosingModule().(ModuleWithPointsTo).getAnExport() = v.getId()
```

and a module-level `__all__` that is a plain list of string literals defines
its exports. (A computed `__all__` trips `complex_all`, which also silences the
rule, but for the wrong reason — use a literal list.)

Changes:

1. `apps/web-server/server/database/alembic/script.py.mako` — add, after the
   four identifiers:

   ```python
   # Alembic reads these via its script loader, never via Python references;
   # listing them as exports tells CodeQL py/unused-global-variable so (#1257).
   __all__ = ["revision", "down_revision", "branch_labels", "depends_on",
              "upgrade", "downgrade"]
   ```

   Every future migration inherits it.
2. The 10 existing files under
   `apps/web-server/server/database/alembic/versions/` — add the same
   `__all__` block. Pure addition; no revision identifier changes, so the
   migration graph is untouched.
3. The 40 open alerts close on their own when CodeQL re-scans `dev`/`main` and
   no longer finds them — no hand dismissal.

This changes the intent's assumed mechanism (a path-scoped filter): the intent
left the mechanism to this spec, and the approved outcome — zero alerts on
migrations, rule active everywhere else — is met with a smaller change.

## Alternatives rejected

- **`query-filters: exclude py/unused-global-variable`** — rule-wide, not
  path-scoped; would silence it across the whole Python tree. Also directly
  violates `codeql-config.yml:31` ("An exclude here WITHOUT a twin query would
  be silencing the rule, and is not allowed").
- **`paths-ignore: alembic/versions/`** — drops *all* analysis of migrations,
  including security queries over migration SQL. Rejected in the intent
  review (narrower option chosen).
- **SARIF post-filter action** (`advanced-security/filter-sarif`) — genuinely
  path-and-rule scoped, but adds a third-party action and a second place where
  alerts are silenced, for a problem the source can fix itself.
- **Inline suppression comments** — GitHub code scanning does not reliably
  honour `# lgtm`/`# codeql[...]` comments; and it would still be suppression.

## Risks

- **Alembic behaviour:** `__all__` only affects `from module import *`, which
  Alembic never does — it loads each script as a module and reads attributes.
  No runtime effect. Verified by running the migration chain (below).
- **Ruff:** `__all__` with names defined in the module is valid; `ruff check`
  and `ruff format --check` must stay green on the touched files.
- **Other analysis:** none — no config file changes.
- **AIFactory:** same fix is copyable there; not done in this task.

## Verification

- `alembic upgrade head` then `alembic downgrade base` on a scratch SQLite DB
  succeeds before and after (the graph is identical).
- `alembic revision -m probe` generates a file containing the `__all__` block
  (template works), then the probe file is deleted.
- CodeQL on the PR: zero `py/unused-global-variable` alerts on
  `alembic/versions/*`. After merge to `dev`, the 40 existing alerts show as
  closed/fixed in code scanning.
- The rule still fires elsewhere: a scratch module with an unused global,
  pushed on a throwaway branch, still raises the alert (mutation check that
  the rule itself is live).

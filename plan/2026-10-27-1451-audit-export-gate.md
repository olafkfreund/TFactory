---
status: approved
issue: 1451
spec: spec/2026-10-27-1451-audit-export-gate.md
---

# Plan: audit-export org gate

Decisions (from the spec):
- Middleware marks the legacy-token and DISABLE_AUTH principals
  `is_service: True` and the `acw_` key principal with `api_key_id`, `org_id`.
- The export needs admin in `org_id` (DB `OrgMember`), an `acw_` key only its
  own org; without `org_id` only `is_service` may export. No role claim trusted.

## Steps

1. `apps/web-server/server/auth.py` (`TokenAuthMiddleware.dispatch` and the acw_ branch; fix the stale class docstring that says the legacy
   token sets `request.state.user = None`):
   add the markers.
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_audit_export_gate.py -q`
   Traps: never `git add -A`; the `.venv` symlink must not be staged.
2. `apps/web-server/server/routes/audit.py::export_audit_logs`: add the gate
   (spec Design 2), import `Request` and `_role_level`; drop the unused
   `ROLE_LEVELS` import. Add `tests/test_audit_export_gate.py` (SQLite, a
   tiny middleware sets `request.state.user`, overrides `audit.get_db` and
   `audit.get_current_user`, plus one test through the real middleware).
   Update the export description in `techdocs/specs/tfactory-web-api.openapi.json`
   to the new docstring (hand edit; no generator or CI check exists).
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_audit_export_gate.py -q`
   and ruff on touched files.

## Tests

As above; the new tests fail with the dev versions of `auth.py`/`audit.py`.

## Rollback

Revert the PR.

## Deviations

- Added `test_acw_key_principal_carries_its_org` (lead review): it drives the real
  `TokenAuthMiddleware` with a stubbed `acw_` key and checks `api_key_id`/`org_id`
  reach `request.state.user`. It fails with the dev `auth.py`.

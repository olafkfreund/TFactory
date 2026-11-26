---
status: approved
issue: 1702
spec: spec/2026-11-26-1702-settings-save-role.md
coders:
  1: codex   # small authz rule in provisioning, security-relevant (owner decision: Codex codes)
  2: codex   # small frontend result plumbing (owner decision: Codex codes)
reviewers:
  - fable    # owner decision: a Fable agent manages and reviews
---

# Plan: the org owner keeps owner rights, and a refused settings save says why

## Approved decisions

- **The recorded owner stays owner.** In `jit_provision_user`, the `OrgMember` role is `owner` when
  `org.owner_id == user.id`, and the claim-mapped role otherwise. This applies on create and on
  sync. `User.role` is unchanged.
- **The settings dialog shows the server's reason.** A new `updateProjectSettingsResult` returns
  `{ ok, error }`, and `updateProjectSettings` wraps it, so its other callers keep a boolean.
  `handleSave` shows `error`, or the generic text when there is none.

## Steps

1. **`apps/web-server/server/oidc/provisioning.py`, around lines 137-160.**
   - Compute `member_role` after the org lookup, and use it in both `OrgMember` branches and the
     log lines.
   - Tests go in a new `apps/web-server/tests/test_oidc_owner_pin.py`, using the async DB fixture
     pattern from `test_oidc_silent.py` or `test_oidc_id_token_hardening.py`. Cover:
     - the owner with a `member` claim stays `owner`, on create and on sync;
     - another user syncs to `member`;
     - a non-owner `factory-admins` user becomes `owner`.

   Verify:
   ```bash
   /mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest apps/web-server/tests/test_oidc_owner_pin.py apps/web-server/tests/test_oidc_silent.py apps/web-server/tests/test_oidc_id_token_hardening.py apps/web-server/tests/test_project_settings_save_route.py -q
   ```
   Run it from the repo root with the PYTHONPATH the existing tests need. Then run ruff check and
   ruff format --check (`apps/backend/.venv/bin/ruff`).

   Traps:
   - no new dependency;
   - keep the log lines free of tokens.
2. **`apps/frontend-web/src/stores/project-store.ts` (~428) and
   `components/project-settings/hooks/useProjectSettings.ts` (~280).**
   - Add `updateProjectSettingsResult`, and make `updateProjectSettings` wrap it.
   - `handleSave` shows `error || 'Failed to save settings'`.
   - Add a vitest for the hook or the store: a failure shows the detail, and a success returns ok.

   Verify, from `apps/frontend-web`: `npx vitest run <new test>`, then `npm run typecheck`.

## Tests

The verify commands above pass, along with the full `apps/web-server/tests/test_oidc*` suite.

## Rollback

Revert the PR. The owner is demoted again at the next login, and the generic error returns.

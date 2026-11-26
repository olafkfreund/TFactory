---
status: draft
issue: 1702
intent: intent/2026-11-26-1702-settings-save-role.md
---

# Spec: the org owner keeps owner rights, and a refused settings save says why

## Design

### 1. Owner pin in JIT provisioning (`apps/web-server/server/oidc/provisioning.py`, `jit_provision_user`)

After `org = await _get_or_create_default_org(db, user)`, the membership role is:

```python
member_role = "owner" if org.owner_id == user.id else role
```

`member_role` replaces `role` in both the create branch and the sync branch for `OrgMember`.
`User.role`, the global field set at creation, is unchanged. Everyone else syncs from the claim as
today, so a demotion in the IdP still takes effect for them.

### 2. Surface the error (frontend)

- `stores/project-store.ts`: a new
  `updateProjectSettingsResult(projectId, settings): Promise<{ ok: boolean; error?: string }>`
  holds today's body and returns `result.error` on failure. `updateProjectSettings` becomes
  `(await updateProjectSettingsResult(...)).ok`, so the other callers are unchanged.
- `useProjectSettings.ts` `handleSave` calls `updateProjectSettingsResult` and shows
  `error || 'Failed to save settings'`.

## Alternatives rejected

- **Set `APP_OIDC_DEFAULT_ROLE=admin`.** That raises every user.
- **Edit `org_members` by hand.** The next login overwrites it.
- **Only fix Keycloak groups.** That is still the owner's ops fix, but the code shouldn't demote the
  recorded owner either way.
- **Disable Save for non-admins.** It's extra UI the bug doesn't need. The server reason is enough.

## Risks

- **The owner pin keeps the recorded owner as owner** even if the IdP removes them from
  `factory-admins`. This is intended: ownership transfer goes through `organizations.owner_id`.
- **The error text comes from the server.** It is shown as plain text.

## Verification

- **Backend tests:**
  - a login whose claim maps to `member` keeps the `owner_id` user as `owner`, on both create and
    sync;
  - another user still syncs to the claim role;
  - a non-owner in `factory-admins` becomes `owner`, as today.
- **Frontend vitest:** a failed save shows the server detail; a success closes the dialog.
- **The existing tests:** `test_oidc_*` and the reproduction test
  `test_project_settings_save_route.py` pass.
- **Live:** after the release, the owner logs in, saves Memory settings, and gets 200.

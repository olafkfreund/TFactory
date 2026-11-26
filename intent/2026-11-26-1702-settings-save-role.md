---
status: draft
issue: 1702
author: olafkfreund
---

# Intent: the org owner keeps owner rights, and a refused settings save says why

## Problem

Saving project settings in the TFactory portal shows "Failed to save settings", and nothing is
saved.

- **The route refuses the request.** It needs project role `admin` and returns 403.
- **The owner is demoted at every login.** OIDC just-in-time provisioning sets the org membership
  role from the IdP `groups` claim each time. The owner's token has no `factory-admins` group, so
  the role becomes `member`, even though `organizations.owner_id` is this user. Nobody in the
  default org is admin, so nobody can save project settings.
- **The dialog hides the reason.** The frontend drops the server's 403 detail and shows generic
  text.

## Proposed outcome

- The user named in `organizations.owner_id` stays `owner` of that org, whatever the IdP groups
  claim says.
- A failed settings save shows the server's reason, for example "This action requires at least
  'admin' project role".

## Affected users and systems

- TFactory web server: `server/oidc/provisioning.py`.
- TFactory frontend: project settings save (`stores/project-store.ts`,
  `components/project-settings/hooks/useProjectSettings.ts`).
- The org owner. Other users are unaffected.

## Constraints

- Other users' roles still follow the IdP claim.
- No role is ever raised above what the claim gives, except for the recorded owner.
- No data migration. The fix takes effect at the owner's next login.
- A normal TFactory release.

## Open questions

None. Approved unseen by the owner (2026-11-26).

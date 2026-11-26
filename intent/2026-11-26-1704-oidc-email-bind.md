---
status: draft
issue: 1704
author: olafkfreund
---

# Intent: an SSO login binds to an existing account only on a verified email

## Problem

When no TFactory user matches an OIDC login's `sub`, `jit_provision_user`
(`server/oidc/provisioning.py`) binds the login to any existing user with the same `email`. Three
flaws:

- **The email may be unverified.** `email_verified` is never checked, so an IdP account carrying an
  unverified copy of someone's email takes over that user.
- **An already-linked account is re-linked.** The bind overwrites a user whose `oidc_sub` is
  already set to a different value, so a second IdP identity silently replaces the first.
- **Local registration can plant an account.** It has no email verification. An attacker can
  pre-register a victim's email with a password. When the victim later signs in through SSO, the
  login binds to the attacker's account, and the attacker keeps password access to it.

Since #1702, if that row is the org's recorded owner, the login also keeps `owner`.

## Proposed outcome

- An SSO login binds to an existing account by email only when the ID token says
  `email_verified: true` and the account has no `oidc_sub` yet.
- An account already bound to another `oidc_sub` is never re-bound.
- Binding makes the account SSO-only: its local password is cleared, so a planted password stops
  working.
- A refused bind gives a clear 403. No second user is created, because emails are unique.

## Affected users and systems

- TFactory web server: `server/oidc/provisioning.py`, `server/routes/oidc_routes.py`
  (`oidc_callback`).
- Users who registered locally before SSO keep working, as long as their IdP marks the email
  verified, which the Keycloak realm does.
- AIFactory and PFactory have the same code. They are tracked as separate issues, with the same
  fix.

## Constraints

- No schema change.
- Logins that match by `sub` are unaffected.
- No email or token value is written to new log lines beyond what is logged today.
- A normal TFactory release.

## Open questions

None. Approved unseen by the owner (2026-11-26).

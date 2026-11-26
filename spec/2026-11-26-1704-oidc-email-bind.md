---
status: draft
issue: 1704
intent: intent/2026-11-26-1704-oidc-email-bind.md
---

# Spec: an SSO login binds to an existing account only on a verified email

## Design

### 1. `server/oidc/provisioning.py`

A new exception class, `OIDCBindRefused(Exception)`.

In `jit_provision_user`, the email fallback becomes:

```python
existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
if existing is not None:
    if userinfo.get("email_verified") is not True:
        raise OIDCBindRefused("email not verified")
    if existing.oidc_sub and existing.oidc_sub != sub:
        raise OIDCBindRefused("account already linked")
    existing.oidc_sub = sub
    existing.password_hash = ""  # binding makes the account SSO-only
    user = existing
```

- The check is `is True`, so a missing claim or the string `"true"` refuses.
- The log line stays the same, but without the email: `"OIDC bound existing local user to
  sub=%s"`.
- The "create a new user" branch is reached only when no user has that email. Today's behaviour
  for new users is unchanged.

### 2. `server/routes/oidc_routes.py`, `oidc_callback`

Around the `jit_provision_user` call, catch `OIDCBindRefused` and raise `HTTPException(403,
"This email belongs to an existing account that can't be linked to this sign-in. Sign in the way
you did before, or ask an admin.")`. The response is the same for both reasons, so nothing leaks
about the account. The reason is logged at WARNING without the email.

## Alternatives rejected

- **Create a second user on a refused bind.** `User.email` is unique, so this would raise
  IntegrityError. It would also split one person into two accounts.
- **Keep the local password after a bind.** That leaves a planted password working.
- **Trust the userinfo endpoint instead of the ID token.** The callback already uses the verified
  ID token (`authorize_access_token` checks `iss`, `aud`, `exp` and the algorithm). Its
  `email_verified` claim is the one to trust.

## Risks

- **A user whose IdP doesn't send `email_verified: true` can't link a pre-existing local
  account.** They get a 403 and an admin has to act. The Keycloak realm sets it, and other IdPs
  usually do too.
- **A linked user loses password login.** This is intended.

## Verification

- **Unit tests** in a new `apps/web-server/tests/test_oidc_email_bind.py`, using the async SQLite
  fixture pattern of `test_oidc_owner_pin.py`:

  | Case | Expected |
  |---|---|
  | verified email, account with no `oidc_sub` | binds, and `password_hash` is cleared |
  | `email_verified` missing, false, or `"true"` | refused |
  | account already linked to another `sub` | refused |
  | the same `sub` on the next login | matches by `sub`, unchanged |
  | no existing user | a new user is created, as today |

- **A callback test:** a refusal returns 403 with the fixed detail, and the log has no email.
- **Existing tests:** `test_oidc_owner_pin.py`, `test_oidc_id_token_hardening.py` and
  `test_oidc_silent.py` pass.

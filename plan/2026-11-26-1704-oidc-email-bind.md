---
status: draft
issue: 1704
spec: spec/2026-11-26-1704-oidc-email-bind.md
coders:
  1: codex   # security-relevant authn rule (owner decision: Codex codes)
reviewers:
  - fable    # owner decision: a Fable agent manages and reviews
---

# Plan: an SSO login binds to an existing account only on a verified email

## Approved decisions

- **Email bind conditions.** It needs `userinfo.get("email_verified") is True` and an existing
  account whose `oidc_sub` is empty or already equals `sub`.
- **Binding makes the account SSO-only.** The bind sets `oidc_sub` and clears `password_hash`.
- **Refusal.** `OIDCBindRefused` is raised from `jit_provision_user`, and `oidc_callback` turns it
  into a 403 with one fixed detail for both reasons. The reason is logged without the email.
- **Everything else is unchanged:** the sub match, new-user creation, and the owner pin from #1702.

## Steps

1. Edit two files, `apps/web-server/server/oidc/provisioning.py` (the email fallback, ~lines
   108-116) and `apps/web-server/server/routes/oidc_routes.py` (around the `jit_provision_user`
   call, ~198).
   - Add the new test file `apps/web-server/tests/test_oidc_email_bind.py`, covering the spec's
     table, plus a callback-level 403 test that reuses the helpers of `test_oidc_silent.py` or
     `test_oidc_id_token_hardening.py`.
   - Verify: from the repo root, with `PYTHONPATH=apps/web-server`, run
     ```bash
     /mnt/code/Source-home/GitHub/TFactory/apps/web-server/.venv/bin/pytest apps/web-server/tests/test_oidc_email_bind.py apps/web-server/tests/test_oidc_owner_pin.py apps/web-server/tests/test_oidc_id_token_hardening.py apps/web-server/tests/test_oidc_silent.py -q
     ```
     Then run ruff check and format --check
     (`/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/ruff`). Run the ratchet the
     way CI does: put the CI-pinned mypy 1.20.1 and ruff 0.15.17 first on PATH, with the repo
     requirements installed, and run `python scripts/ratchet_lint.py --base origin/dev --package
     apps/backend --package apps/web-server --package scripts`. It must report 0 regressions.
   - Traps:
     - `test_oidc_owner_pin.py` only exists on the #1702 branch. If this branch doesn't have it,
       copy only its SQLite fixture pattern.
     - In tests, `httpx` `Response` types can differ between httpx builds; use `cast`, as #1702
       did.

## Tests

The verify commands above.

## Rollback

Revert the PR. The unverified bind comes back.

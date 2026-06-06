# Test-target authentication — log in, then test

> TFactory can store credentials for a **system-under-test** (a staging app, a
> SaaS/CRM, anything behind a login) encrypted at rest, and inject them into the
> sandbox **only** for egress-enabled lanes so generated tests can authenticate
> — without secrets pasted into test files. Resolution is **off by default** and
> gated by the same per-project egress opt-in as the credential broker.
>
> Epic [#107](https://github.com/olafkfreund/TFactory/issues/107).
> Spec: `.agent-os/specs/2026-06-02-test-target-auth-credential-vault/`.
> Distinct from the **infra** [Credential Broker](./credentials.md) (#62), which
> authenticates *agents* to cloud environments — this is for the *test target*.

## TL;DR

```text
1. Store the credential (encrypted, org-scoped) — portal Settings or the API:
     POST /api/test-credentials  { name, kind, username, secret }   → returns an id (tc_…)

2. Reference it from .tfactory.yml (egress MUST be enabled):
     egress: { enabled: true }
     test_credentials:
       login: { ref: store:tc_…, as_secret: TEST_PASSWORD, as_username: TEST_USERNAME }
     targets:
       - name: web
         type: http
         base_url: https://staging.example.com
         auth: { type: ref, ref: login, login_url: /login, ... selectors ... }

3. A subtask that needs auth is tagged `requires_auth: true`; the browser lane
   logs in once (Playwright storageState) and reuses the session.
```

## Where credentials come from (the `ref`)

Each `test_credentials` entry names a secret **reference**; the resolved value
becomes the `as_secret` env var inside the sandbox (and `username_ref` →
`as_username`):

| Scheme | Resolved by | Example |
|---|---|---|
| `store:<id>` | the **web-server** (it owns the encrypted DB) | `store:tc_a1b2` |
| `env:NAME` | the credential broker | `env:STAGING_PW` |
| `vault:path#field` | the credential broker | `vault:secret/staging/app#password` |

> The backend agent runs in a separate venv without the DB driver, so
> `store:<id>` refs are resolved web-server-side at hand-off and arrive as
> plain env; `env:`/`vault:` refs are resolved by the backend broker.

## Storing a credential

The portal stores test-target credentials **encrypted at rest** (the same
`EncryptedString` / KMS·Vault·Azure·GCP backends as Git credentials). The
secret is **never returned by the API after creation** — only metadata.

```bash
curl -sX POST http://localhost:3102/api/test-credentials \
  -H 'Content-Type: application/json' \
  -d '{"name":"staging-login","kind":"form","username":"qa@acme.test","secret":"•••••"}'
# → {"id":"tc_a1b2","name":"staging-login","kind":"form","username":"qa@acme.test", ...}
```

`kind` is one of `form` · `api_token` · `basic_auth` · `totp`.

## `.tfactory.yml` schema

```yaml
version: 1

egress:
  enabled: true            # REQUIRED — login needs network egress (fail-closed otherwise)

test_credentials:
  login:
    ref: store:tc_a1b2     # store:<id> | env:NAME | vault:path#field
    kind: form
    as_secret: TEST_PASSWORD     # env var the secret is exposed as
    as_username: TEST_USERNAME   # optional: env var for the username
    # username_ref: env:STAGING_USER   # optional: resolve the username from a ref

targets:
  - name: web
    type: http
    base_url: https://staging.example.com
    auth:
      type: ref            # references a test_credentials entry by name
      ref: login
      login_url: /login
      username_selector: "#email"
      password_selector: "#password"
      submit_selector: "button[type=submit]"
      success_url_pattern: "**/dashboard"
```

Validation is **fail-closed**: declaring `test_credentials` without
`egress.enabled` is an error, and a `ref`-auth must name a declared entry.

### Multi-step / SSO logins (`steps`)

The single-step selectors above cover a plain form login. For **SSO / IdP-redirect
/ multi-step** logins (e.g. "Login with SSO" → enter email → Next → password →
submit), declare an ordered `steps` list instead — it drives the login and owns
the navigation (no `login_url` needed):

```yaml
    auth:
      type: ref
      ref: login
      steps:
        - { action: goto, url: https://staging.example.com }
        - { action: click, selector: "text=Login with SSO" }
        - { action: fill_username, selector: "#email" }   # reads the injected username env var
        - { action: click, selector: "#next" }
        - { action: fill_secret, selector: "#password" }  # reads the injected secret env var
        - { action: fill, selector: "#tenant", value: acme-corp }  # non-secret literal only
        - { action: click, selector: "button[type=submit]" }
        - { action: wait_for_url, url: dashboard }
```

Actions: `goto` (`url`) · `click` (`selector`) · `fill_username` / `fill_secret`
(`selector`, value from the injected env var) · `fill` (`selector` + `value`,
**non-secret literals only**) · `wait_for_url` (`url` substring/glob). Credentials
are **never inlined** — `fill_username`/`fill_secret` read the vault-injected env
vars at run time. The login still runs once and is reused via `storageState`.

## Security model

- **At rest:** encrypted via `EncryptedString` (KMS / Vault / Azure KV / GCP SM).
- **In flight:** injected only as ephemeral container env (`-e`) for
  egress-enabled lanes; **wiped after every run**; hermetic lanes (unit /
  mutation, `--network=none`) get nothing.
- **No leakage:** resolved secret values are scrubbed from logs / junit / the
  HAR / verdicts / triage by the `Redactor` (`tfactory_secrets/redaction.py`).
- **Never returned:** the portal API returns credential metadata only.

## Status

The credential **vault** and config surface are implemented and tested:

| Piece | Status |
|---|---|
| Encrypted storage + `/api/test-credentials` | ✅ (#107 task 1) |
| Backend broker resolver (`env:`/`vault:`) | ✅ (task 2) |
| `.tfactory.yml` `test_credentials` + ref-auth + `requires_auth` | ✅ (task 3) |
| Web-server `store:` resolver | ✅ (task 4a) |
| Schema → resolver spec glue | ✅ (task 4b-glue) |
| **Executor injection + sink redaction wiring** | ⏳ pending (task 4b-final) |
| **Playwright `storageState` login fixture** | ✅ (#235, epic #232) |
| **Portal Settings UI** | ⏳ pending (task 7) |

Until the remaining steps land, declaring `test_credentials` is parsed and
validated but not yet consumed end-to-end by a running pipeline. Track progress
on [#107](https://github.com/olafkfreund/TFactory/issues/107).

## Playwright storageState login (#235)

`agents/evidence/layout.py::scaffold_auth_setup` renders a Playwright **setup**
file (`tests/auth.setup.ts`) plus a `playwright.config.ts` from the target's
`auth: { type: ref }` block — single-step selectors (`render_auth_setup`) or an
ordered `steps` list for SSO (`render_auth_setup_steps`). It drives the login
with the credential's injected env vars (`as_username` / `as_secret`, never
inlined) and saves the session to `.auth/state.json`; the generated config's
chromium project `dependencies: ['setup']` + `use.storageState` so authed specs
log in **once** and reuse the session.

Gen-Functional calls this automatically (`_maybe_scaffold_auth`) when a browser
subtask is `requires_auth: true`; non-authed lanes are untouched. Because the
`setup` project re-runs at the start of every run, an expired token simply
re-authenticates on the next run — there is no stale-session reuse across runs.

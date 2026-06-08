# test-target-authentication

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: testing, authentication, login, playwright, storagestate, sso, secrets, credential-broker, egress

---

# Test Target Authentication

Use this skill when generated tests must log into the application-under-test: declaring `test_credentials` and ref-auth in `.tfactory.yml`, scaffolding single-step form login or multi-step SSO with `LoginStep`, reusing a Playwright `storageState` so you log in once and replay the session, resolving secrets through the credential broker (`env:` / `store:` / `vault:`), respecting the egress gate, and never inlining a password into a test.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Test Target Authentication

Most real apps are behind a login. TFactory's browser/api lanes need to authenticate against the target *without* ever writing a secret into generated test code. This skill covers the auth declaration in `.tfactory.yml`, the login scaffolding (single form + multi-step SSO), the log-in-once-then-reuse pattern via Playwright `storageState`, and how the credential broker resolves `env:` / `store:` / `vault:` references and wipes them after the run.

The non-negotiables: secrets come from the broker as `TEST_USERNAME` / `TEST_PASSWORD`, never inlined; creds are ephemeral and wiped post-run; egress to the IdP must be allowed by the egress gate.

---

## When to use this skill
- The target requires a login before browser/api tests can do anything useful.
- Declaring `test_credentials` / a `ref-auth` block in `.tfactory.yml`.
- Scaffolding a login: single-step username/password form, or multi-step SSO with redirects.
- Wanting Playwright to log in once and reuse the session across specs (`storageState`).
- Wiring secrets via the broker (`env:`, `store:`, `vault:`) instead of literals.
- Tokens that must refresh per run (short-lived bearer / OAuth client-credentials).

Do NOT trigger for:
- Bringing the app up / health-gating it (that is `test-environment-orchestration`).
- Sandbox isolation, mount, and resource rules (that is `sandbox-and-test-security`).
- Cloud account access for posture scans (that is the cloud-discovery access gate).

---

## Key principles
1. **Never inline a secret** — A literal password in a generated test is a leak and a review-failure. Secrets are *referenced*, resolved at runtime, injected as env.
2. **Broker resolves references** — `env:NAME`, `store:key`, `vault:path#field` are resolved by the credential broker into `TEST_USERNAME` / `TEST_PASSWORD` (and friends) inside the lane container.
3. **Log in once, reuse** — Use `scaffold_auth_setup` to drive login a single time, save Playwright `storageState`, and have every spec start authenticated. Don't log in per-test.
4. **Ephemeral creds, wiped after run** — Resolved credentials live only for the run; the broker wipes them when the sandbox tears down. Nothing persists to disk in the repo.
5. **Egress gate must allow the IdP** — SSO redirects to an external IdP need `egress.enabled` plus the IdP host allowed, or the redirect dies.
6. **Multi-step login is data, not code** — SSO flows are described as an ordered list of `LoginStep`s (fill, click, wait), not bespoke imperative code, so they're reproducible and reviewable.
7. **Refresh tokens per run** — Short-lived tokens are minted at run start (e.g. client-credentials grant), not baked into a fixture, so a stale token never causes a phantom auth failure.

---

## Core concepts
**ref-auth** — The `.tfactory.yml` auth block: a *reference* to where credentials come from (broker refs) plus the login recipe (single-step or steps).

**test_credentials** — The named credential set for the target, each value a broker ref. Resolved into env vars (`TEST_USERNAME`, `TEST_PASSWORD`, `TEST_TOKEN`).

**credential broker** — Resolves `env:` / `store:` / `vault:` refs at run start, injects them as env into the lane container, and wipes them at teardown.

**LoginStep** — One step of a multi-step (SSO) flow: an action (`fill`, `click`, `press`, `wait_for`) on a selector/URL. Ordered steps model redirects across the app and IdP.

**scaffold_auth_setup** — The generator seam that emits a Playwright auth-setup project: it runs the login *once*, then `storageState` is saved and reused by dependent projects.

**storageState** — Playwright's serialized cookies + localStorage. Saved after the one login; every test project loads it so specs start logged in.

**egress gate** — `egress.enabled` (+ allowed hosts) governs whether the lane may reach the IdP. SSO needs it on.

**broker ref schemes** — `env:NAME` (run environment), `store:key` (TFactory credential store), `vault:path#field` (HashiCorp Vault path + field). All resolved at run start, injected as env, wiped at teardown.

**single-step vs multi-step** — A single-step login is a form on one page (fill, fill, submit). A multi-step (SSO) login is an ordered `LoginStep` chain that follows redirects across the app and an external IdP and back.

---

## Common tasks

### Declare single-step form login (broker-backed creds)
```yaml
# .tfactory.yml
target:
  type: http
  base_url: https://app.example.com
auth:
  type: form
  test_credentials:
    username: env:TEST_USERNAME      # resolved by broker -> env
    password: vault:secret/app/test#password
  login:
    url: /login
    steps:
      - fill:  { selector: "#email",    value: "$TEST_USERNAME" }
      - fill:  { selector: "#password", value: "$TEST_PASSWORD" }
      - click: { selector: "button[type=submit]" }
      - wait_for: { selector: "[data-testid=dashboard]" }
```

### Declare multi-step SSO (LoginStep chain across IdP)
```yaml
auth:
  type: sso
  test_credentials:
    username: store:okta-test-user
    password: store:okta-test-pass
  login:
    url: /login
    steps:
      - click:    { selector: "button#sso-login" }        # app -> IdP redirect
      - wait_for: { url: "https://idp.example.com/**" }
      - fill:     { selector: "#okta-username", value: "$TEST_USERNAME" }
      - click:    { selector: "#okta-next" }
      - fill:     { selector: "#okta-password", value: "$TEST_PASSWORD" }
      - click:    { selector: "#okta-verify" }
      - wait_for: { url: "https://app.example.com/dashboard" }   # back to app
egress:
  enabled: true
  allow: ["idp.example.com"]   # IdP must be reachable for the redirect
```

### Log in once, reuse via storageState (the generated shape)
`scaffold_auth_setup` emits a setup project that other projects depend on:
```typescript
// auth.setup.ts — runs ONCE
import { test as setup } from '@playwright/test';
const file = 'playwright/.auth/user.json';
setup('authenticate', async ({ page }) => {
  await page.goto(process.env.TFACTORY_TARGET_URL + '/login');
  await page.fill('#email', process.env.TEST_USERNAME!);
  await page.fill('#password', process.env.TEST_PASSWORD!);
  await page.click('button[type=submit]');
  await page.waitForSelector('[data-testid=dashboard]');
  await page.context().storageState({ path: file });
});
```
```typescript
// playwright.config.ts — every test starts authenticated
projects: [
  { name: 'setup', testMatch: /auth\.setup\.ts/ },
  { name: 'chromium',
    use: { storageState: 'playwright/.auth/user.json' },
    dependencies: ['setup'] },
]
```

### Mint a fresh API token per run (no stale tokens)
```yaml
auth:
  type: oauth_client_credentials
  test_credentials:
    client_id:     env:TEST_CLIENT_ID
    client_secret: vault:secret/app/test#client_secret
  token:
    url: https://idp.example.com/oauth/token
    refresh_per_run: true        # mint at run start, never reuse across runs
```

### Reference secrets from each broker source
```yaml
test_credentials:
  a: env:TEST_PASSWORD                 # from the run's environment
  b: store:my-app/test-user            # from the TFactory credential store
  c: vault:secret/data/app#password    # from HashiCorp Vault path#field
```

### Reuse storageState across api and browser lanes
The api lane can replay the same session by pulling cookies/headers out of the saved state, so you log in once for *both* lane types.
```typescript
// api test: hydrate request context from the browser login's storageState
import { request } from '@playwright/test';
const ctx = await request.newContext({ storageState: 'playwright/.auth/user.json' });
const res = await ctx.get(process.env.TFACTORY_TARGET_URL + '/api/me');
```

### Inject creds as env, reference by name (never the literal ref)
```yaml
auth:
  type: form
  test_credentials:
    username: vault:secret/app/test#username
    password: vault:secret/app/test#password
  login:
    steps:
      # CORRECT: reference the resolved env var, not the vault path
      - fill: { selector: "#user", value: "$TEST_USERNAME" }
      - fill: { selector: "#pass", value: "$TEST_PASSWORD" }
```

### Scope the test account to least privilege
Use a dedicated test identity, not a human/prod account — and store its refs in the broker.
```yaml
test_credentials:
  username: store:app/test-bot       # dedicated, least-privilege identity
  password: store:app/test-bot-pass
```

---

## Gotchas
1. **`$TEST_PASSWORD` not expanding** — The broker injects creds as *env vars* into the lane container. Steps must reference `$TEST_PASSWORD` (resolved at run), not the literal vault ref. Mixing the two leaks or breaks login.
2. **storageState expires mid-suite** — A long run can outlive a short session cookie. If late specs start failing auth, shorten the suite or re-run the setup project, or use a token with a longer TTL.
3. **SSO redirect blocked by egress** — `egress.enabled: true` alone isn't enough if the IdP host isn't allowed. Add the IdP to `egress.allow` or the redirect hangs and login `wait_for` times out.
4. **Secret committed via storageState** — `playwright/.auth/user.json` contains live session tokens. It must be gitignored and lives only in `/scratch`; never commit it.
5. **Wrong wait after SSO** — After the final IdP step you must `wait_for` the *app* URL/selector, not the IdP's. Waiting on the IdP page races the redirect back.
6. **Per-test login storms the IdP** — Logging in inside every test (not via the setup project) can rate-limit or lock the test account. Always log in once.
7. **Token minted but not refreshed** — Without `refresh_per_run: true`, a cached token from a previous run can be expired, producing intermittent 401s that look like flaky tests.

8. **Vault path resolves but field is wrong** — `vault:secret/app/test#password` needs the exact `#field`. A typo'd field resolves to empty, and login fails with a confusing "wrong password" rather than a missing-secret error.

9. **storageState saved before login completed** — Calling `storageState()` before the post-login `wait_for` lands means you persist a logged-*out* session. Always wait for the authenticated marker first, then save.

10. **Broker ref committed to `.tfactory.yml` is fine — the value isn't** — `vault:...`/`store:...` refs are safe to commit (they're pointers). A resolved value must never reach git. Confirm CI logs don't echo `$TEST_PASSWORD`.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Inlining a password literal in a generated test | Secret leak; fails review; rotates poorly | Reference via broker (`env:`/`store:`/`vault:`) → `TEST_PASSWORD` |
| Logging in inside every test | Slow, brittle, account lockout / IdP rate-limit | Log in once with `scaffold_auth_setup`; reuse `storageState` |
| Committing `playwright/.auth/user.json` | Live session tokens land in git history | Gitignore it; keep it in `/scratch` only |
| `wait_for` the IdP page after the last SSO step | Races the redirect back to the app | `wait_for` the app's post-login URL/selector |
| Caching a bearer token across runs | Stale token → intermittent 401s read as flakes | `refresh_per_run: true` to mint fresh each run |
| Off-box IdP with egress disabled | SSO redirect can't reach the IdP; login hangs | `egress.enabled: true` + IdP host in `egress.allow` |
| Storing creds in the repo `.env` and committing | Same as inlining, just one level removed | Use the broker; resolve at runtime, wipe after run |
| Reusing a real prod account for tests | Side-effects + lockout risk on prod | Dedicated test account, broker-resolved, least-privilege |

# Spec Tasks

These are the tasks for the spec in @.agent-os/specs/2026-06-02-test-target-auth-credential-vault/spec.md

> Created: 2026-06-02
> Status: Ready for Implementation
> Tracking issue: #107

Phased so each parent task is independently shippable behind the egress/`requires_auth` opt-ins (default off → zero behaviour change until wired end-to-end).

## Tasks

- [ ] 1. **Credential storage (web-server)**
  - [ ] 1.1 Write tests for `TestTargetCredential` (encryption-at-rest, `UNIQUE(org_id,name)`, no-secret serialisation)
  - [ ] 1.2 Add the model (mirror `GitCredential`) + Alembic migration
  - [ ] 1.3 `POST/GET/DELETE /api/test-credentials` (metadata-only responses, org-scoped authz mirroring `git_credentials.py`)
  - [ ] 1.4 Migration green on `postgres (P1)` PG 15 + 16; verify all tests pass

- [ ] 2. **Resolver in `tfactory_secrets`**
  - [ ] 2.1 Write tests for `resolve_test_target_credentials()` (env/vault/store refs, hermetic→none, egress-off→none, wipe)
  - [ ] 2.2 Implement it alongside `resolve_cloud()`; `store:<id>` via direct `_EncryptedString` decryption (option A)
  - [ ] 2.3 Tag the resolver test module into `CRITICAL_MODULES`; verify all tests pass

- [ ] 3. **`.tfactory.yml` schema + subtask field**
  - [ ] 3.1 Write tests for the `test_credentials` block + `targets[].auth.ref` validation, and subtask `requires_auth`
  - [ ] 3.2 Extend `tfactory_yml/schema.py` + the `test_plan` subtask schema (`requires_auth: bool = false`)
  - [ ] 3.3 Fail-closed validation when `test_credentials` used without egress; verify all tests pass

- [ ] 4. **Executor wiring + redaction**
  - [ ] 4.1 Write tests: executor injects creds for egress browser/api lanes only; secret scrubbed from logs/junit/HAR/verdicts/triage
  - [ ] 4.2 Extend `sandbox_credentials.resolve_sandbox_credentials()` to merge test-target creds; reuse `wipe()`
  - [ ] 4.3 Add the redaction pass (highest risk: HAR `Authorization`/cookie/form-body in `http_recorder.py`)
  - [ ] 4.4 Verify hermetic lanes still get nothing; verify all tests pass

- [ ] 5. **Playwright auth (`storageState`)**
  - [ ] 5.1 Write a runner smoke test: `auth.setup.ts` logs in once → `state.json`; protected test reuses it (no second login in HAR)
  - [ ] 5.2 Add `auth.setup.ts.tmpl` + update `playwright.config.tmpl.ts` (setup project + `use.storageState`, gated on `requires_auth`)
  - [ ] 5.3 Rewrite `frameworks/playwright/templates/login-flow.spec.ts.tmpl` to read injected env vars
  - [ ] 5.4 Verify the smoke test passes in the playwright runner image

- [ ] 6. **Planner / Gen-Functional `requires_auth`**
  - [ ] 6.1 Write tests: Planner tags auth subtasks; Gen-Functional emits storageState-backed tests; Evaluator runs setup first
  - [ ] 6.2 Implement the tagging + generation path (prompt + context_block updates)
  - [ ] 6.3 Verify all tests pass

- [ ] 7. **Portal UI**
  - [ ] 7.1 Settings → Credentials panel (create/list-metadata/delete) reusing the Git-credentials UI pattern; i18n en/fr/pt-BR
  - [ ] 7.2 `npm run typecheck` + component tests; verify green

- [ ] 8. **Docs + guide**
  - [ ] 8.1 `guides/test-target-auth.md` (store a credential → reference in `.tfactory.yml` → mark a subtask `requires_auth`)
  - [ ] 8.2 Update `.tfactory.yml.example` + `docs/framework-registry.md` where relevant; close #107

## Definition of done
- All acceptance criteria in `spec.md` met; no plaintext secret in any log/artifact/verdict/triage (redaction test green).
- Defaults unchanged: hermetic lanes get no creds; the whole feature is opt-in via egress + `requires_auth`.
- Backend `critical` + `postgres` CI lanes + frontend typecheck green.

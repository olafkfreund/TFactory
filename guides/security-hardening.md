# Security hardening — TFactory test pipeline

> Epic #232 / #242. Living checklist for keeping the test pipeline bullet-proof.

TFactory runs generated code against real targets and can write to repos, so its
attack surface is the sandbox, the egress path, the inbound webhook, and the
commit/PR side-effects. This guide records the controls in place and the review
checklist.

## Controls in place

### Sandbox (Executor)
- `tools/runners/docker_runner.py` runs tests with `--network=none` by default;
  only egress lanes (browser/api/integration/cloud) opt into `--network=bridge`.
- Code-under-test is mounted **read-only** (`/work:ro`); only `/scratch` is
  writable. The Docker socket is never mounted.
- Test-target credentials are materialised as ephemeral env/files, mounted
  read-only, and **wiped after the run** (`sandbox_credentials.py`).

### Egress / credentials
- `.tfactory.yml` `egress.enabled` must be true to declare `test_credentials`
  (fail-closed validation).
- Credential refs resolve via the broker (`env:` / `store:` / `vault:`); secrets
  are never inlined in generated tests or login fixtures (#107 / #235).

### Inbound handback webhook (#182, hardened #242)
- Gated by `APP_INBOUND_HANDBACK_ENABLED`; 404 when off.
- Shared-secret in `X-TFactory-Handback-Token`, compared with
  `hmac.compare_digest` (constant-time — no timing leak).
- **Rate-limited** per task (`FixedWindowLimiter`, 10/min) — a leaked secret
  can't drive unbounded re-fires; also bounded by `TFACTORY_HANDBACK_MAX_CYCLES`.
- Idempotency guard: an in-flight run returns `already_running` (no double-fire).

### Repo side-effects (Triager)
- Git commit + PR comment are **dry-run by default**; opt-in via
  `TFACTORY_TRIAGER_GIT_WRITE=1` / `TFACTORY_TRIAGER_PR_COMMENT=1`.
- **Optional GPG-signed commits** via `TFACTORY_TRIAGER_GIT_SIGN=1` (#242) —
  requires `user.signingkey` configured in git.
- No automatic pushes to remotes.

### Badges / facts (read-only outward)
- The public badge endpoint (`/api/badges/...`) and the Backstage emitter expose
  only aggregate counts (accept-rate / readiness), never test content.

## Review checklist (run each release)
- [ ] No new lane runs with `--network=bridge` unless it genuinely needs egress.
- [ ] No new code path mounts the Docker socket or writes outside `/scratch`.
- [ ] Every new secret flows through the broker; none logged (sink redaction).
- [ ] New inbound endpoints are gated + authenticated + rate-limited, with
      constant-time secret comparison.
- [ ] New repo side-effects honour the dry-run-by-default policy.
- [ ] Base-image CVEs triaged (e.g. #218); runner images rebuilt.

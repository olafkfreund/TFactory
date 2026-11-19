---
status: approved
issue: 1617
intent: intent/2026-11-19-1617-tf-flake-ratelimit.md
---

# Spec: authenticated nix fetches in lane Jobs, honest nix-failure verdict

## Design

1. `tools/runners/kube_sandbox.py` `build_job_manifest`: new optional kwarg
   `nix_github_secret: tuple[str, str] | None` (Secret name, key). When set the
   lane container gets `TF_NIX_GITHUB_TOKEN` from `secretKeyRef` and
   `NIX_CONFIG="access-tokens = github.com=$(TF_NIX_GITHUB_TOKEN)"` (Kubernetes
   expands `$(VAR)`; the token is not in the manifest or command line, and nix
   does not print it. Test code in the Job could print its own env: see Risks).
   Unset (default): no env added, manifest unchanged.
2. `agents/nix_env.py` `nix_runner_from_env`: reads `TFACTORY_NIX_GITHUB_SECRET`
   (+ optional `TFACTORY_NIX_GITHUB_SECRET_KEY`, default `github-token`) into
   that kwarg. All lanes share this builder, so every lane benefits.
3. `charts/tfactory`: `rbac.jobSandbox.nixGithubSecret` (default empty) renders
   the env var only when set.
4. `agents/nix_shell_lane.py`: `bats_job_script` first prints
   `__TF_JOB_STARTED__`. In `run_bats_lane_via_nix`, if that marker is absent
   (nix failed before the script ran) the result is `125` (not_run) with
   `stderr` = `nix environment failed before tests ran: ` + the nix `error:`
   lines (fallback: last lines), capped. Missing exit markers after the script
   started keep today's exit 1 `missing exit marker` (test evidence preserved).
   A repo `flake.lock`, when present, is already honoured (`nix develop path:`).

## Alternatives rejected

- Generate/commit a lock per task: needs nix in the backend pod and still
  fetches on first lock; the token fixes all unlocked inputs at once.
- Pass the token as a plain env value: lands in the Job spec.
- Reclassify every missing marker as not_run: would hide real test failures
  that happen after tests started (Codex review).

## Risks

- Generated test code runs in the Job and can read the token env. The owner
  should supply a token with no scopes (public-read rate limit only). Egress is
  already restricted by NetworkPolicy. No output redaction is added here; the
  confidentiality guarantee is the token's lack of scopes, not log filtering.
- A configured but missing Secret fails the Job at start (non-optional ref):
  intended, loud, and off by default.

## Verification

Unit tests: manifest with/without the kwarg (secretKeyRef, `$(TF_NIX_GITHUB_TOKEN)`
ordering, no literal token); `nix_runner_from_env` with/without env; bats lane
returns 125 and surfaces the nix error when the started marker is missing, and
keeps 1 `missing exit marker` when it is present, and the stability classifier
buckets that 125 result as environment (consumer-level check); helm template renders env only
when set; `scripts/ratchet_lint.py` for backend and web-server.

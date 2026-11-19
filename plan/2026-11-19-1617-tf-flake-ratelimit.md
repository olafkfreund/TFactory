---
status: draft
issue: 1617
spec: spec/2026-11-19-1617-tf-flake-ratelimit.md
coders:
  1: coder   # contained manifest edit + test; claude does it directly (2 files)
  2: coder   # env wiring + chart, mechanical
  3: codex   # small tricky logic: started-marker classification in the shell lane
reviewers:
  - codex    # independent read-only review of the final diff
---

# Plan: authenticated nix fetches in lane Jobs, honest nix-failure verdict

Approved decisions (spec): optional Secret-backed `access-tokens` for lane Jobs
(off by default, no token in manifest text); shell lane returns 125 not_run with
the real nix error only when `__TF_JOB_STARTED__` is absent; a missing exit
marker after the script started stays exit 1. No log redaction; token should be
scope-less. repo `flake.lock` already honoured.

## Steps

1. `apps/backend/tools/runners/kube_sandbox.py` `build_job_manifest`: add kwarg
   `nix_github_secret: tuple[str, str] | None = None`; when set, append to
   `container.setdefault("env", [])` first `TF_NIX_GITHUB_TOKEN` (valueFrom
   secretKeyRef name,key) then `NIX_CONFIG` = `access-tokens = github.com=$(TF_NIX_GITHUB_TOKEN)`.
   Test in `tests/test_kube_sandbox.py` (with/without; order; no literal token).
   → `pytest tests/test_kube_sandbox.py -q`
2. `apps/backend/agents/nix_env.py` `nix_runner_from_env`: read
   `TFACTORY_NIX_GITHUB_SECRET` (+ `_KEY`, default `github-token`) into
   `kw["nix_github_secret"]` (widen the `kw` annotation at nix_env.py:359 to `dict[str, Any]`);
   `apps/backend/agents/verify_dispatch.py` ~L812-826: add both vars to the forwarded
   `_nix_var` tuple (the verify Job runs the nested lane Job; without this it stays
   anonymous) + assertions in `tests/test_verify_dispatch.py` ~L321-335; `charts/tfactory/values.yaml` + `templates/deployment.yaml`:
   `rbac.jobSandbox.nixGithubSecret` renders `TFACTORY_NIX_GITHUB_SECRET` only when set.
   Test the env reader. → `pytest tests/test_kube_sandbox.py tests/test_nix_env*.py tests/test_verify_dispatch.py -q`; `helm template` shows env only with the value set.
3. `apps/backend/agents/nix_shell_lane.py`: `bats_job_script` first echoes
   `__TF_JOB_STARTED__`; `run_bats_lane_via_nix`: if absent -> `code=125`,
   `why = "nix environment failed before tests ran: " + nix error lines (cap 400)`
   and skip `shell_verdict`. Migrate the existing fake outputs in `tests/test_nix_shell_lane.py` (~L112-128,
   160-169, and the no-marker test ~L131) to include `__TF_JOB_STARTED__` where the
   run is simulated as started. New tests: absent marker
   -> 125 with the 403 text; present marker + missing exit marker -> still 1;
   stability classifier maps the result to environment.
   → `pytest tests/test_nix_shell_lane.py tests/test_stability_runner*.py -q`

Traps: implementers edit and test only; the lead stages and commits. Pre-commit may
fail on an unrelated mcp-library collection error: after committing, the lead runs
`python3 scripts/ratchet_lint.py --base origin/dev --package apps/backend` and
`... --package apps/web-server` (committed-diff gate) and commits with `--no-verify`, saying why. Plain imports (no `as` aliases in parens).

## Tests

`pytest tests/test_kube_sandbox.py tests/test_nix_shell_lane.py tests/test_nix_env_services.py -q`; ruff on touched files; both ratchet_lint runs.

## Rollback

Revert the PR. The Secret env is off by default, so no live state depends on it.

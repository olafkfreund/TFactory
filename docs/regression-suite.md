---
title: Regression Suite
layout: default
---

# Regression suite and continuous verification (RFC-0018)

TFactory generates and verifies tests for a feature once. The regression suite
re-runs a project's persisted test corpus over time and diffs each run against a
stored baseline, so a change that breaks tests already on `main` is caught
without anyone re-triggering verification.

See [RFC-0018](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0018-regression-suite-and-continuous-verification.md)
for the design. This page is the operator/developer guide.

## What a run does

1. **Load the corpus** — read `.tfactory/tests-catalog.json` (the persistent
   cross-run catalog) into runnable units.
2. **Execute** — run each test on the Nix-flake-per-task Kubernetes Job
   substrate (RFC-0005 Tier A — the same `nix_provisioner` + `kube_sandbox`
   path AIFactory build/verify uses). The toolchain comes from the contract's
   `environment` manifest, so the regression environment matches the build
   environment with no drift. The runner refuses to silently fall back to the
   in-pod host venv.
3. **Diff against the baseline** — classify every test:

   | Class | Meaning |
   |---|---|
   | `regression` | passed in the baseline, fails now (gate-failing) |
   | `fixed` | failed in the baseline, passes now |
   | `still_failing` | failed in both |
   | `stable_pass` | passed in both |
   | `flaky` | history-classified as flaky (see quarantine) |
   | `quarantined` | excluded from the gate |
   | `new` | not in the baseline |
   | `dropped` | in the baseline, gone from the corpus |

4. **Persist + report** — store the run under
   `<workspace>/<project>/regression/<run_id>.json` and write
   `<run_id>-report.{md,json}` (which also carries the coverage-drift block).

## Robustness

- **Within-run retry** — a test that fails then passes within the same run is
  recorded as passed (a transient blip), not a false regression. Configurable
  attempts (default 2; 1 disables).
- **Cross-run flaky quarantine** — a test whose pass/fail history flips above a
  threshold over enough runs is quarantined: excluded from the gate, still run
  and reported, releasable by an operator, and auto-released once it stabilises.
- **Coverage trend + drift** — project coverage is recorded per run; a drop
  beyond the threshold is flagged in the report.
- **Impact-based selection** — re-run only the subset covering a change instead
  of the whole corpus; a partial run scopes its baseline so unselected tests are
  not mis-classified as `dropped`.

## How to run it

### CLI

```bash
python -m agents.regression run \
  --project <project_id> \
  --repo-root <checked-out worktree> \
  --workspace <workspace root> \
  [--commit <sha>] \
  [--lanes unit,api] \
  [--changed-acs "AC#1,AC#2"] \
  [--changed-files tests/foo.py] \
  [--flaky-store <path to flaky history json>]
```

Exit code is `1` when any test regressed, `0` otherwise — so a CI step can gate
on it.

### Nightly schedule (Kubernetes)

A default-off CronJob ships in the Helm chart. Enable it in values:

```yaml
regressionSchedule:
  enabled: true            # requires workspaces.enabled=true
  schedule: "0 3 * * *"
  projects: [myapp, shop]  # each must have a worktree at <mountPath>/<project>
```

### On demand (HTTP)

```
POST /api/projects/{project_id}/regression/run   -> 202 { run_id, status }
GET  /api/projects/{project_id}/regression        -> the read-model
```

The `POST` runs in the background and returns the `run_id`; the unattended path
is the nightly CronJob.

### On demand (MCP)

The standalone MCP server publishes a `regression_run` tool
(`{ project_id, commit? }`) that runs synchronously and returns the run summary.

## Portal

The task-detail view has a **Regression** tab backed by
`GET /api/projects/{id}/regression`: the latest verdict, current
regressions/fixes, the quarantine list, and run history.

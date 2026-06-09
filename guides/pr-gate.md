# PR Quality Gate

> Turn TFactory's verdicts into a **red/green merge check** on the PR — making
> "Test" a gate in the workflow your team already uses. WS1 of the enterprise
> 90-day plan (`.agent-os/product/enterprise-90day-plan.md`).

## What it does

When the Triager reaches a terminal status, it evaluates
`findings/verdicts.json` against a policy and publishes a GitHub **commit
status** named **`TFactory / tests`** on the PR's head commit:

- ✅ **success** — the generated suite cleared the bar.
- ❌ **failure** — it didn't; the status description says why (e.g.
  `Gate failed: 1 accepted, 2 flagged, 0 rejected (accept-rate 33%)`).

The status links back to the PR, and the full triage report still posts as a PR
comment alongside it. Branch protection can then *require* the `TFactory / tests`
check to block merge.

## Enable it (two opt-in switches)

The gate is **off by default** and **dry-run by default** — consistent with
TFactory's no-automatic-pushes posture. You opt in at two levels:

### 1. Policy — in the repo's `.tfactory.yml`

```yaml
version: 1
targets: []          # your usual targets

quality_gate:
  enabled: true       # master switch (default: false)
  min_accepted: 1            # require ≥ this many accepted tests
  min_accept_rate: 0.5       # accepted / evaluated must be ≥ this (0..1)
  max_flag_rate: 1.0         # flagged / evaluated must be ≤ this (0..1)
  block_on_reject: false     # fail if any test was rejected (rejects are
                             # dropped junk, not SUT bugs — usually leave off)
  block_on_survived_mutation: true   # fail if an *accepted* test survived its mutation probe
  block_on_mocked_subject: true      # fail if an *accepted* test mocks its own subject (ci_parity)
  require_stable_accepts: true       # fail if an *accepted* test isn't 3×-stable
  context: "TFactory / tests"        # the status-check label shown on the PR
```

Every field is optional; the defaults above apply when omitted. With only
`enabled: true`, the gate requires **at least one accepted test** and enforces
the three per-accepted-test guardrails (no survived mutant, no mocked subject,
stable only) — those should never trip if the Evaluator did its job, so they're
a safety net against a contradictory verdict slipping through.

### 2. Publishing — the `TFACTORY_PR_STATUS` env flag

| `TFACTORY_PR_STATUS` | Behaviour |
|---|---|
| unset / `0` (default) | **Dry-run** — the gate is computed and recorded in `status.json.pr_status`, but no status is posted. |
| `1` | **Publish** the commit status to GitHub. |

So you can roll it out safely: enable the policy first, watch the computed
verdicts in `status.json`, then flip `TFACTORY_PR_STATUS=1` once you trust it.

## What the gate reads

The policy is graded against each verdict's `signals_summary` (written by the
Evaluator):

| Signal | Gate use |
|---|---|
| `verdict` (`accept`/`flag`/`reject`) | counts → `min_accepted`, `min_accept_rate`, `max_flag_rate`, `block_on_reject` |
| `mutation` (`killed`/`survived`/…) | `block_on_survived_mutation` on accepted tests |
| `ci_parity` (`yes`/`mocked-subject`/…) | `block_on_mocked_subject` on accepted tests |
| `stability` (`stable`/`flaky`/…) | `require_stable_accepts` on accepted tests |

## GitHub permissions

The commit status is published with `gh api` against
`repos/<owner>/<repo>/statuses/<sha>`, which needs a token with the
**`repo:status`** scope (a fine-grained token with *Commit statuses: write* also
works). No GitHub App required — the commit-status API is deliberately the
lowest-friction path for self-hosted installs. `gh` runs from the project repo
dir; `repo_slug` + `sha` come from `context/source.json`.

> The richer **Checks API** (annotations, re-run button) is a future
> enhancement; commit statuses are enough to gate merge today.

## Where the result lands

Every run records a `pr_status` summary in `status.json`, whether dry-run or
live:

```json
"pr_status": {
  "skipped": false,
  "passed": true,
  "state": "success",
  "summary": "Gate passed: 4 accepted, 1 flagged, 0 rejected (accept-rate 80%)",
  "reasons": [],
  "counts": {"accept": 4, "flag": 1, "reject": 0, "total": 5},
  "dry_run": true,
  "ok": true,
  "argv": ["gh", "api", "-X", "POST", "repos/acme/widgets/statuses/<sha>", "..."]
}
```

`skipped: true` with a `reason` means the gate didn't run — typically
`quality_gate not enabled`, `no sha/repo in source.json`, or
`gate not evaluated` (missing/invalid verdicts).

## Recommended rollout

1. Add a minimal `quality_gate: { enabled: true }` to `.tfactory.yml`.
2. Run a few tasks; inspect `status.json.pr_status` (still dry-run).
3. Tune `min_accept_rate` to your tolerance.
4. Set `TFACTORY_PR_STATUS=1` to publish.
5. In GitHub branch protection, mark **`TFactory / tests`** a required check.

## Implementation

- Gate logic: `apps/backend/agents/quality_gate.py` (`evaluate_gate` / `GatePolicy`).
- Status publisher: `apps/backend/tools/pr_status.py` (`post_pr_status`, dry-run-first).
- Wiring: `apps/backend/agents/triager.py` (`_run_pr_status_side_effect`).
- Policy schema: `apps/backend/tfactory_yml/schema.py` (`QualityGatePolicy`).

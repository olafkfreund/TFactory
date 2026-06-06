# ci-cd-test-automation

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: testing, ci-cd, automation, github-actions, merge-gating, completion-event, webhook, flaky-quarantine, artifacts

---

# CI/CD Test Automation

Use this skill when wiring TFactory test generation and execution into CI/CD: triggering a run on a PR, gating merges on the verdict and confidence, consuming the RFC-0001 completion-event webhook instead of polling, parallelising lanes, publishing artifacts and evidence (triage report, junit, coverage, verdicts), quarantining flaky tests via cross-run flip-rate history, and distinguishing required vs advisory checks.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# CI/CD Test Automation

TFactory is most useful when its verdict participates in the merge decision. This skill covers triggering a run from CI, waiting for it *without polling* via the RFC-0001 completion-event webhook, turning the triage verdict into a pass/fail gate, parallelising the lanes, surfacing the evidence as CI artifacts, and quarantining chronically flaky tests so they advise rather than block.

The principle: TFactory produces a *verdict with confidence and evidence*. CI's job is to decide which checks are required (block merge) vs advisory (inform), and to consume the completion event rather than babysit a long-running job.

---

## When to use this skill
- Adding a TFactory test step to a GitHub Actions (or other CI) PR pipeline.
- Gating merge on the triage verdict / confidence and on the completion `outcome`.
- Consuming the completion-event webhook (RFC-0001 envelope) instead of polling task status.
- Parallelising lanes (unit / browser / api / integration / mutation) for wall-clock speed.
- Publishing the triage report, junit.xml, coverage.xml, and verdicts.json as CI artifacts.
- Quarantining flaky tests using cross-run flip-rate history (`flaky_history.py`).
- Deciding which TFactory checks are required vs advisory on a branch.

Do NOT trigger for:
- Declaring the test target / health gate (that is `test-environment-orchestration`).
- App login plumbing (that is `test-target-authentication`).
- Sandbox isolation internals (that is `sandbox-and-test-security`).

---

## Key principles
1. **Gate on the verdict, not the exit code** — A green `pytest` exit doesn't mean the tests are *good*. Gate on the Triager verdict (accept/flag/reject) and confidence, which fold in coverage delta, stability, mutation, and semantic relevance.
2. **Event, don't poll** — Configure the completion webhook (RFC-0001 envelope) so CI is notified when the task reaches a terminal status. Polling `task_status` in a loop wastes runner minutes and races.
3. **Parallelise lanes** — Lanes are independent; run them as a matrix. Wall-clock is min, not sum. Aggregate verdicts after.
4. **Evidence is a first-class output** — Always upload the triage report + junit + coverage + verdicts as artifacts. A merge decision must be auditable.
5. **Flaky → quarantine, not block** — Cross-run flip-rate history flags chronically flaky tests; they become advisory until stable. A flake must never block a clean PR.
6. **Required vs advisory is deliberate** — Pick which checks block merge. New/experimental lanes (e.g. mutation) start advisory; promote to required once trustworthy.
7. **Dry-run side-effects in CI** — TFactory's git-write/PR-comment default OFF. Opt in *only* on the integration branch where you want the report posted; never on forks.

---

## Core concepts
**triage verdict + confidence** — The Triager's per-test accept/flag/reject and the run's confidence. The merge-gate input, richer than a junit pass/fail.

**completion-event envelope (RFC-0001)** — The normalized cross-service JSON POSTed on terminal status: `schema_version`, `event`, `service`, `correlation_id` (the GitHub issue #), `outcome`, plus legacy flat fields. Documented in `docs/completion-event-envelope.md`.

**TFACTORY_COMPLETION_WEBHOOK** — URL TFactory POSTs the envelope to on terminal status (timeout via `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT`, default 5s). The CI hook.

**TFACTORY_COMPLETION_SENTINEL** — Writes `findings/COMPLETED.json` a same-host CI step can `stat` instead of polling.

**lane matrix** — unit / browser / api / integration / mutation run in parallel; verdicts aggregated.

**flaky_history.py (#37)** — Persists each test's pass/fail across runs (`test_history.json`) → flip-rate; chronically flaky tests are flagged even if one 3× stability pass slips through.

**required vs advisory checks** — Branch-protection required status checks block merge; advisory ones report only.

**artifacts/evidence** — `findings/triage_report.{md,json}`, `verdicts.json`, junit.xml, coverage.xml uploaded per run.

**outcome (terminal status)** — The run's terminal state carried in the envelope: `triaged`, `triaged_empty`, or `triager_failed`. CI maps these to pass / neutral / fail respectively.

**handback loop in CI** — On failing tests, TFactory can prepare an AIFactory correction (`findings/handback_request.*`). In an autonomous pipeline, `/tfactory-fixloop` drives a bounded test→fix→re-test cycle gated by `TFACTORY_HANDBACK_MAX_CYCLES` (→ `stuck` when capped).

---

## Common tasks

### Trigger TFactory on a PR and gate the merge (GitHub Actions)
```yaml
# .github/workflows/tfactory.yml
name: tfactory-tests
on: { pull_request: { branches: [main, dev] } }
jobs:
  tests:
    strategy:
      matrix: { lane: [unit, api, browser, integration] }   # parallel lanes
      fail-fast: false
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run TFactory lane
        run: python apps/backend/run.py --spec ${{ github.event.number }} --lane ${{ matrix.lane }}
      - name: Upload evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: tfactory-${{ matrix.lane }}
          path: |
            ~/.tfactory/workspaces/**/findings/triage_report.*
            ~/.tfactory/workspaces/**/findings/verdicts.json
            ~/.tfactory/workspaces/**/**/junit.xml
            ~/.tfactory/workspaces/**/**/coverage.xml
```

### Gate the merge on the verdict (not the raw exit code)
```yaml
  gate:
    needs: tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - name: Enforce verdict
        run: |
          # reject => fail the required check; flag => advisory annotation
          python - <<'PY'
          import json, glob, sys
          rejects = 0
          for f in glob.glob('**/verdicts.json', recursive=True):
              for v in json.load(open(f)).get('verdicts', []):
                  if v['verdict'] == 'reject': rejects += 1
          sys.exit(1 if rejects else 0)
          PY
```

### Consume the completion event instead of polling (RFC-0001)
```bash
# Tell TFactory where to POST the terminal-status envelope:
export TFACTORY_COMPLETION_WEBHOOK="https://ci.example.com/hooks/tfactory"
export TFACTORY_COMPLETION_WEBHOOK_TIMEOUT=5
```
The receiver matches on the envelope:
```json
{ "schema_version": "1", "event": "task.completed", "service": "tfactory",
  "correlation_id": "224", "outcome": "triaged" }
```
On-runner alternative (same host) — sentinel instead of a webhook:
```bash
export TFACTORY_COMPLETION_SENTINEL=1   # writes findings/COMPLETED.json
until [ -f "$WS/findings/COMPLETED.json" ]; do sleep 5; done
```

### Quarantine flaky tests (advisory, not blocking)
```bash
# flaky_history.py persists flip-rate across runs; high flip-rate => quarantine
python apps/backend/agents/flaky_history.py --report \
  --workspace "$WS" --threshold 0.2   # >20% flip-rate => flagged, advisory
```

### Mark which checks are required vs advisory (branch protection)
```bash
gh api -X PATCH repos/:owner/:repo/branches/main/protection/required_status_checks \
  -f 'contexts[]=tfactory-tests / gate' \
  # leave 'tfactory-tests / mutation' OFF the list => advisory until trusted
```

### Post the triage report as a PR comment (trusted branch only)
Opt in to the side-effect explicitly, and only on a same-repo PR (never a fork).
```yaml
  comment:
    needs: gate
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - name: Post triage report
        env:
          TFACTORY_TRIAGER_PR_COMMENT: "1"   # explicit opt-in; default OFF
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh pr comment ${{ github.event.number }} \
               --body-file $(ls **/triage_report.md | head -1)
```

### Map terminal outcome to a CI conclusion
```bash
# from the completion envelope's "outcome"
case "$OUTCOME" in
  triaged)        echo "pass" ;;                 # tests generated + scored
  triaged_empty)  echo "neutral"; exit 0 ;;      # nothing to test => don't block
  triager_failed) echo "fail";    exit 1 ;;      # pipeline error => block
  stuck)          echo "needs-human"; exit 1 ;;  # fixloop cap hit
esac
```

### Drive an autonomous fix loop (bounded)
```bash
export TFACTORY_HANDBACK_PREPARE=1        # build the correction artifact (default ON)
export TFACTORY_HANDBACK_SEND=1           # POST it to AIFactory (opt-in)
export TFACTORY_HANDBACK_MAX_CYCLES=2     # cap → status=stuck for a human
# /loop 60s /tfactory-fixloop <task_id>   # one cycle per invocation
```

---

## Gotchas
1. **Polling burns runner minutes** — A `until task_status==done` loop on a long pipeline wastes CI time and can time out the job. Use the completion webhook or sentinel.
2. **Green junit, bad tests** — junit "passed" doesn't capture mutation survival or low semantic relevance. If you gate only on junit, weak tests merge. Gate on the verdict.
3. **`correlation_id` mismatch** — The envelope's `correlation_id` is the GitHub *issue* number, not the PR number. Match your receiver to the right id or events go unrouted.
4. **Flaky test blocks a clean PR** — If a flaky test is a *required* check, an unrelated PR fails. Route flip-rate-flagged tests to advisory via quarantine.
5. **Webhook timeout too tight** — Default 5s. A slow receiver drops the event silently (it's best-effort — a failing target never breaks the pipeline). Make the receiver fast or raise the timeout.
6. **Side-effects fire on forks** — Enabling `TFACTORY_TRIAGER_PR_COMMENT` for fork PRs leaks the token / fails. Keep side-effects off for `pull_request` from forks; use `pull_request_target` carefully or post from the gate job only.
7. **Mutation lane gating too early** — Mutation is the slowest, noisiest signal. Making it required on day one blocks merges on flaky mutation results. Start advisory, promote later.

8. **`triaged_empty` treated as failure** — When there's nothing to test, the outcome is `triaged_empty`, not an error. Mapping it to `fail` blocks innocuous PRs. Map it to neutral / pass.

9. **Fixloop runs unbounded** — Without `TFACTORY_HANDBACK_MAX_CYCLES`, a stubborn failure can ping-pong AIFactory↔TFactory forever and exhaust CI. Always cap it; a hit cap should surface `stuck` for a human.

10. **Webhook receiver returns slow/5xx** — The completion POST is best-effort with a 5s default timeout; a slow receiver silently drops the event and CI never learns the run finished. Keep the receiver fast and idempotent.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Gating merge on `pytest` exit code only | Misses mutation/coverage/relevance — weak tests pass | Gate on the Triager verdict + confidence |
| Polling `task_status` in a CI loop | Wastes runner minutes; races; can time out | Consume the RFC-0001 completion webhook or sentinel |
| Running all lanes serially | Wall-clock = sum of lanes; slow feedback | Run lanes as a parallel matrix; aggregate after |
| Not uploading evidence | Merge decisions aren't auditable; can't debug failures | Always upload triage report + junit + coverage + verdicts |
| Making a flaky test a required check | Unrelated PRs fail on noise | Quarantine via flip-rate history; flaky = advisory |
| Making mutation required from day one | Noisy/slow signal blocks merges prematurely | Start mutation advisory; promote once trusted |
| Enabling git-write/PR-comment on fork PRs | Token leak / permission failure | Side-effects off for forks; post from the trusted gate job |
| Matching the webhook on PR number | Envelope `correlation_id` is the issue #, not PR # | Route the receiver on the issue number |

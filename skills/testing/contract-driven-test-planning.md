# contract-driven-test-planning

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: rfc-0002,task-contract,test-planning,planner,declared-config,lanes,coverage-target,mutation-scope,correlation-key,ac-to-code-map

---

# Contract-Driven Test Planning

Use this skill when the Planner should plan tests from an explicit RFC-0002 Task Contract `tfactory` block instead of inferring from the diff — declaring lanes, frameworks, endpoints, coverage_target, mutation_scope, security_scope, and ac_to_code_map. Covers the precedence rule (declared config WINS over inference), fallback to inference when fields are absent, and how the correlation_key threads the contract through the pipeline.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Contract-Driven Test Planning

By default TFactory's Planner *infers* what to test from the AIFactory spec and the diff. But inference is a guess. When an upstream tool (PFactory, AIFactory) ships an RFC-0002 Task Contract with a `tfactory` block, that block is an explicit declaration of intent — lanes, frameworks, endpoints, coverage targets, mutation scope, and an AC-to-code map. The contract is authoritative: **declared config wins over inference.** This skill covers consuming that block, the precedence/fallback rules, and the correlation_key that ties it all together.

---

## When to use this skill
- An incoming task carries an RFC-0002 Task Contract with a `tfactory` block and you want the Planner to honor it.
- Deciding precedence when the contract and the inferred plan disagree (contract wins).
- Handling a *partial* contract — some fields declared, others fall back to inference.
- Mapping `ac_to_code_map` so each acceptance criterion's tests target the right code.
- Threading the `correlation_key` so verdicts, triage, and handback all reconcile to the same task.
- Do NOT trigger for: evaluating test quality (test-quality-evaluation), the no-contract / inference-only path (that's the default Planner flow), or cloud posture targets (cloud-posture-testing uses `.tfactory.yml cloud_provider`, not the RFC-0002 lane block).

---

## Key principles
1. **Declared config wins over inference** — every field present in the `tfactory` block overrides whatever the Planner would have guessed. The contract is intent stated by an upstream that knows more than the diff reveals.
2. **Absence means fall back, not skip** — a missing field is not "disable that lane"; it means "infer it as usual". Precedence is field-by-field, not all-or-nothing.
3. **Partial contracts are normal** — most contracts declare a few high-value fields (lanes, coverage_target) and leave the rest to inference. Honor the declared ones; infer the gaps.
4. **The AC-to-code map is the spine** — `ac_to_code_map` ties each acceptance criterion to the code that implements it, so the Planner generates tests that target the right symbols and the Evaluator can judge semantic relevance against the real claim.
5. **correlation_key is the thread, not the payload** — it identifies the task across services (often the GitHub issue #) so verdicts, the triage report, the completion-event envelope, and any handback all reconcile to one task.
6. **Validate the block before trusting it** — a malformed or contradictory contract (e.g. a framework that doesn't match its lane) should be flagged, not silently obeyed.
7. **Scopes bound work, they don't invent it** — `mutation_scope` and `security_scope` *narrow* where those signals run; they never grant new capabilities (app SAST/DAST stays out of scope per DEC-002 regardless of what a contract says).
8. **Validate before you obey** — declared-wins does not mean trust-blindly. A contract with an incoherent lane/framework pairing or unresolvable `ac_to_code_map` symbols should be flagged, not executed into nonsense subtasks.
9. **One correlation_key, end to end** — the same key threads the plan, the verdicts, the triage report, the completion envelope, and any handback. Losing or changing it mid-pipeline breaks reconciliation and the fix loop.

---

## Core concepts
**Where this sits in the pipeline** — contract consumption happens at the very front, in the Planner (`agents/planner.py`), before any test is written. The Planner normally reads `context/aifactory_spec.md` + `context/diff.patch` and infers lane-tagged subtasks into `test_plan.json`. With an RFC-0002 contract present, the declared `tfactory` block is layered *over* that inference, field by field, so the plan reflects stated intent first and guesses only where the contract is silent.

**RFC-0002 Task Contract** — the cross-service contract describing a task. Its `tfactory` block is the testing-specific section the Planner consumes.

**`tfactory` block fields** —
- `lanes` — which modality lanes to plan (unit / browser / api / integration / mutation).
- `frameworks` — explicit framework per lane (pytest / Jest / Playwright), overriding language inference.
- `endpoints` — API endpoints under test, so the `api` lane targets them directly.
- `coverage_target` — the coverage goal the Evaluator's coverage_delta is measured against.
- `mutation_scope` — which files/symbols mutation testing covers.
- `security_scope` — bounds for security-related checks (within DEC-002 limits — posture/CSPM, not app SAST/DAST).
- `ac_to_code_map` — mapping from each acceptance criterion to the implementing code.

**Precedence (declared > inferred)** — for each field: if present in the block, use it; else infer from spec + diff. The Planner applies this per-field.

**Fallback** — the inference path the Planner already runs (spec + `diff.patch` → lane-tagged subtasks) when a field is absent.

**correlation_key** — the stable task identifier carried through verdicts.json, the triage report, the RFC-0001 completion-event envelope (`correlation_id`), and the handback loop, so every artifact reconciles to one task.

**Why declared beats inferred** — the diff shows *what changed*, not *what matters*. An upstream planner (PFactory) or builder (AIFactory) knows the acceptance criteria, the API surface, and which files are the real risk — knowledge a line-level diff can't express. The contract is that knowledge made machine-readable, so honoring it produces a sharper plan than inference ever could. Inference is the safety net for when the contract is absent or partial, not a co-equal source of truth.

**Lane spine the contract draws from** — `lanes` selects from TFactory's v0.2 modality spine: unit / browser / api / integration / mutation (pytest · Jest · Playwright). A contract declaring `lanes: [browser]` with `frameworks: { browser: Playwright }` tells the Planner to skip unit/api entirely and plan only Playwright browser subtasks — overriding any inference that would have added unit tests off the diff.

**Coverage_target as the Evaluator's yardstick** — the declared `coverage_target` isn't enforced by the Planner; it's handed downstream so the Evaluator's `coverage_delta` signal is measured against the contract's bar rather than a default. Declaring it makes the verdict accountable to the upstream's intent.

---

## Common tasks
### End-to-end: contract to test_plan.json
Walk a full task to see the precedence machinery in action:
1. A task arrives with the RFC-0002 contract below and a diff touching `app/orders/service.py` and `app/orders/coupon.py`.
2. The Planner reads `lanes: [unit, api, mutation]` — it will NOT add a `browser` lane even though it could infer UI from elsewhere, because the contract is explicit.
3. `frameworks` pins pytest for unit/api; the Planner skips language inference for those lanes.
4. `endpoints` points the `api` lane subtasks at the two order routes directly.
5. Each `ac_to_code_map` entry becomes one phase/subtask targeting its symbol.
6. `mutation_scope` limits the mutation lane to `service.py` only.
7. `coverage_target: 0.85` is recorded for the Evaluator to measure against.
8. `correlation_key: gh-224` is stamped onto `test_plan.json` and every downstream artifact.
The output `test_plan.json` is the union of declared intent (authoritative) and inferred fill-ins (only where the contract was silent, e.g. mutation tooling per language).

### Read and honor a `tfactory` block
```yaml
# RFC-0002 Task Contract (excerpt)
tfactory:
  lanes: [unit, api, mutation]
  frameworks: { unit: pytest, api: pytest }
  endpoints: ["/api/orders", "/api/orders/{id}"]
  coverage_target: 0.85
  mutation_scope: ["app/orders/service.py"]
  ac_to_code_map:
    "AC-1 order total includes tax": "app/orders/service.py:compute_total"
    "AC-2 expired coupon rejected":  "app/orders/coupon.py:apply_coupon"
  correlation_key: "gh-224"
```
The Planner plans only the declared lanes, uses the declared frameworks, points the `api` lane at the declared endpoints, and tags each subtask with its AC and target symbol from `ac_to_code_map`.

### Resolve a contract-vs-inference conflict
Declared always wins. If inference would have added a `browser` lane but the contract lists only `[unit, api, mutation]`, drop the browser lane — the upstream decided it's not in scope.

### Handle a partial contract
If the block declares `lanes` and `coverage_target` but omits `frameworks`, honor the lanes and target, and infer frameworks per subtask language as usual. Never treat an omitted field as "off".

### Drive the AC-to-code map into planning
For each `ac_to_code_map` entry, emit a subtask whose target is the mapped symbol and whose phase is that AC. This sharpens both generation (tests hit the right code) and the Evaluator's semantic_relevance (judged against the real claim).

### Thread the correlation_key
Carry `correlation_key` into status/verdicts/triage and the completion-event envelope's `correlation_id` so `/tfactory-watch`, the scorecard, and `/tfactory-fixloop` all reconcile to the same task.

### Validate a contract before planning from it
Before trusting the block, check coherence: every lane in `lanes` should have a sane framework (api/unit → pytest or Jest; browser → Playwright; not Playwright on an `api` lane), `endpoints` should only appear when an `api` lane is declared, and `ac_to_code_map` symbols should resolve in the diff. A failed check flags the contract rather than producing nonsense subtasks.

### Reconcile a contract that over-declares
If the contract lists an `integration` lane but the diff touches no integration surface, honor the declaration but emit a low-priority subtask and note the mismatch — the upstream may know about a surface the diff doesn't show. Don't silently drop a declared lane; declared wins, but surface the tension for a human.

### Map ACs to code for sharper generation and evaluation
Each `ac_to_code_map` entry becomes a subtask whose `target` is the mapped symbol and whose `phase` is that AC. This does double duty: Gen-Functional writes a test that actually exercises the right code, and the Evaluator's semantic_relevance signal is judged against the precise AC claim instead of a guessed association — tightening both ends of the pipeline from one declaration.

### Decide the plan when contract and diff disagree
Build a quick per-field table before planning: for each field, note the declared value and the inferred value, then apply declared-wins.
| Field | Declared | Inferred | Result |
|---|---|---|---|
| lanes | [unit, api] | [unit, api, browser] | [unit, api] (drop browser) |
| frameworks.unit | pytest | pytest | pytest |
| coverage_target | 0.85 | (none) | 0.85 |
| endpoints | ["/orders"] | (none) | ["/orders"] |
| mutation_scope | (absent) | infer from diff | inferred |
The result column is what the Planner writes into `test_plan.json`. Where a declared field conflicts with inference, the declaration silently wins; where it's absent, inference fills in.

### Confirm correlation_key propagation
After planning, grep the workspace to confirm the key threads through:
```bash
grep -r "gh-224" ~/.tfactory/workspaces/<project_id>/specs/<spec_id>/ \
  --include=status.json --include=verdicts.json 2>/dev/null
```
If the key is missing from later artifacts, the completion envelope and `/tfactory-fixloop` won't reconcile — fix propagation before relying on the loop.

---

## Gotchas
1. **Treating an absent field as "disabled"** — omitting `frameworks` doesn't mean "no framework"; it means infer. Falling back is the contract, not skipping.
2. **All-or-nothing precedence** — precedence is per-field. Don't reject the whole contract because one field is missing, and don't ignore a declared field because another is absent.
3. **Trusting a contradictory contract** — a contract listing `frameworks: { api: Playwright }` for an `api` lane is malformed; obeying it produces nonsense. Validate lane/framework coherence and flag mismatches.
4. **Letting `security_scope` widen scope** — a contract can't authorize app SAST/DAST; DEC-002 still holds. `security_scope` only bounds posture-style checks, never grants new app-security capability.
5. **Ignoring `ac_to_code_map`** — without it, the Planner falls back to guessing which code an AC tests, weakening semantic_relevance. Honor the map when present.
6. **Losing the correlation_key mid-pipeline** — if it isn't propagated, handback and watch can't reconcile artifacts to the task. Thread it end-to-end.
7. **Coverage_target as a hard reject everywhere** — a declared target informs the verdict but a marginally-missed target on a low-risk AC may warrant flag, not reject. Use it as the bar the Evaluator measures against, with judgment.
8. **Endpoints declared without an api lane** — listing `endpoints` while omitting `api` from `lanes` is contradictory; the Planner has nowhere to apply them. Validate that `endpoints` only appears alongside a declared `api` lane.
9. **Stale ac_to_code_map after a rebase** — if the contract was authored against an older diff, its mapped symbols may have moved or renamed. Resolve each symbol against the *current* diff before planning; flag entries that no longer resolve rather than generating tests against ghosts.
10. **Treating frameworks as global** — `frameworks` is per-lane. A contract can declare pytest for `unit` and Playwright for `browser` in the same task; don't collapse them to one project-wide framework.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Inferring lanes when the contract declares them | Declared config is authoritative intent | Honor declared lanes; infer only absent fields |
| Treating a missing field as "off" | Absence means fall back to inference | Apply precedence per-field; infer the gaps |
| Obeying a contradictory contract verbatim | Malformed lane/framework pairs produce nonsense tests | Validate coherence; flag mismatches before planning |
| Using `security_scope` to enable app SAST/DAST | DEC-002 keeps app security out of scope | Limit to posture/CSPM; route app security elsewhere |
| Ignoring `ac_to_code_map` | Planner guesses code targets; weak semantic relevance | Emit one subtask per mapped AC→symbol |
| Dropping the correlation_key | Verdicts/triage/handback can't reconcile to the task | Thread correlation_key into every artifact + envelope |
| Rejecting the whole contract on one bad field | Throws away valid declared intent | Validate and use the good fields; flag the bad one |
| Treating coverage_target as an absolute reject | Ignores risk/context of the AC | Measure against it with verdict judgment (flag vs reject) |

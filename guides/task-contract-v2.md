# Task Contract v2 ingest (RFC-0002)

> Epic #244. How TFactory consumes the cross-factory Task Contract's `tfactory`
> VERIFY profile instead of inferring framework/lane/endpoints.

PFactory computes a VERIFY profile, AIFactory carries it in the handover, and
TFactory consumes it. When the contract is present TFactory uses the **declared**
config; when absent it falls back to inference (changed files + `.tfactory.yml`),
so nothing breaks for non-contract runs.

Contract: [RFC-0002](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0002-task-contract.md) ·
schema vendored at `apps/backend/contracts/task-contract-v2.schema.json`.

## The `tfactory` block

```jsonc
{
  "contract_version": "2",
  "correlation_key": "PF-123",      // RFC-0001 shared key
  "tfactory": {
    "lanes": ["unit", "api", "browser"],          // security => out of scope (DEC-002)
    "frameworks": {"unit": "pytest", "browser": "playwright"},
    "endpoints": {"api_base_url": "http://localhost:8000"},
    "docker_compose": "docker-compose.test.yml",
    "coverage_target": 0.85,                        // 0..1
    "mutation_scope": ["src/core/**.py"],
    "security_scope": ["owasp:*"],                  // recorded as delegated
    "ac_to_code_map": {"AC-1": ["src/login.py:login"]}
  }
}
```

## How TFactory ingests it

| Stage | Behaviour | Code |
|---|---|---|
| **Read** (#245) | Find the contract in `context/` (`task_contract.json` → `aifactory_plan.json` → `source.json` embed); parse the consumed subset into `TfactoryProfile`. Absent/empty → `None` (infer). | `agents/task_contract.py` |
| **Plan** (#246) | The initial-mode Planner prompt gets an authoritative **DECLARED TEST PROFILE** block — generate exactly the declared lanes/frameworks/endpoints; infer only the gaps. | `prompts_pkg/prompts.py::_build_contract_profile_block` |
| **Target** (#248) | The profile block renders `ac_to_code_map` (AC → files, capped) so tests are targeted per criterion; `ac_targets()` for programmatic use. | `prompts_pkg/prompts.py`, `agents/task_contract.py` |
| **Scope** (#247) | The Evaluator records `coverage_target` / `mutation_scope` / `security_scope` into `verdicts.json` as `execution_scope` (+ a `coverage_target_met` proxy). `security_scope` is recorded as **delegated** — TFactory never generates SAST/DAST. | `agents/contract_scope.py` |
| **Correlate** (#249) | Completion events + the hand-back reconcile on one key: contract `correlation_key` → GitHub issue # → `tf-<spec_id>`. | `agents/triager.py`, `agents/handback/send.py` |

## Notes
- Everything is **best-effort**: a malformed/absent contract never breaks
  planning, evaluation, or completion — TFactory degrades to inference.
- Security lanes from the contract are acknowledged + delegated, not generated
  (Decision DEC-002).
- See also `guides/testing-model.md` for the end-to-end picture.

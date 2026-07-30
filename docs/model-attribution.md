---
layout: default
title: Model attribution
permalink: /model-attribution/
nav_order: 7.6
---

# Which model actually verified this build

> Closes [#869](https://github.com/olafkfreund/TFactory/issues/869). Refs
> [Factory#295](https://github.com/olafkfreund/Factory/issues/295) (validation
> scorecard) and [Factory#338](https://github.com/olafkfreund/Factory/issues/338).

**User story.** As someone filling in a benchmark cell, I need an artifact that
names the model that ran the verification, so I can state a result without
inferring it from what was requested. A requested model and a resolved model are
not the same fact: a run can fall back, and an invisible fallback is how a
benchmark table ends up crediting a model that never executed.

## Where the evidence lives

Every verify run writes `status.json` in the spec directory. Its `usage` block
carries the run totals and, since #869, a per-phase `workers` list that rides
through onto the completion event CFactory consumes:

```jsonc
"usage": {
  "input_tokens": 206,
  "output_tokens": 82613,
  "total_tokens": 82819,
  "cost_usd": 7.76388,
  "model": "claude-opus-4-8-20260115",   // the id that served the most tokens
  "workers": [
    {
      "worker_id": "coding",             // TFactory's "worker" is a phase
      "phase": "coding",                 // verification runs on `coding`
      "provider": "claude",
      "requested_model": "claude-opus-4-8",       // what the seam asked for
      "model": "claude-opus-4-8-20260115",        // what actually served tokens
      "input_tokens": 206,
      "output_tokens": 82613,
      "total_tokens": 82819,
      "cost_usd": 7.76388
    }
  ]
}
```

This is deliberately the same shape as AIFactory's `token_usage.json` `workers`
map, so CFactory renders attribution from both services identically rather than
learning a second convention.

### requested_model vs model

| Field             | Meaning                                                    | Source                                    |
| ----------------- | ---------------------------------------------------------- | ----------------------------------------- |
| `requested_model` | The id the execution seam resolved and asked for            | `get_phase_model()` / the contract's routed model, read back off the client |
| `model`           | The id that actually served the turns                       | the provider's own response               |

They usually match. When they do not, the run fell back and `model` is the one
to quote. `model` is **never** back-filled from `requested_model`: an empty
`model` honestly means "the provider did not say", and reads as UNKNOWN rather
than as a plausible default.

### Which providers report a resolved model

| Provider                            | Reports resolved model | Source of the id                          |
| ----------------------------------- | ---------------------- | ----------------------------------------- |
| Claude (Agent SDK)                  | Yes                    | `AssistantMessage.model`, and `ResultMessage.model_usage` as the token-weighted fallback |
| Ollama (native + agentic)           | Yes                    | the `model` field Ollama echoes on `/api/chat` |
| OpenAI-compatible (native + agentic)| Yes                    | the `model` field on `/v1/chat/completions` |
| Codex / Gemini / Copilot CLI shims  | No                     | the CLI transport never reports one; the field stays empty |

## There is no `testing` phase

Factory#295 cell B4 is worded "control which model runs TESTING/verify". TFactory
has no `testing` phase and never has. The phase keys are:

```
spec, planning, coding, qa, qa_fixer
```

(`apps/backend/phase_config.py`, `DEFAULT_PHASE_MODELS`.)

Verification -- the Evaluator, which is the lane that produces verdicts -- runs
on the **`coding`** key: `agents/evaluator.py` calls
`get_phase_model(spec_dir, "coding", None)`. The test Planner and Generator use
`planning` and `coding` respectively; the QA lanes use `qa` / `qa_fixer`.

A handoff contract carrying `execution.phase_models.testing` is **silently
dropped**: `agents/tools_pkg/tools/task_control.py` whitelists exactly
`{spec, planning, coding, qa, qa_fixer}` when translating the contract's
`phase_models` into `task_metadata.json`.

**So: to control the model that verifies a build, set `coding`.** B4 is
satisfiable today, but its wording is wrong -- there is no `testing` control
surface to point at, and asking for one produces no error, just no effect.

## What is deliberately not written

`routing.actual` in `contracts/task-contract-v2.schema.json` describes the same
fact and is still written by zero lines of TFactory Python. It stays that way on
purpose: the contract at `context/task_contract.json` is the *signed input* to
the run, and its digest feeds the approval content-hash. Writing run output back
into it would mutate an artifact whose whole value is that it did not change.
The `usage.workers` block above carries the identical audit trail on the
completion event, where consumers already look.

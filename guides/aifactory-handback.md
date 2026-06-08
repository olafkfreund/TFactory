# Hand a correction back to AIFactory (the closed loop)

> **The reverse of the handover.** `/handover-to-tfactory` ships a finished
> feature *to* TFactory for testing; **the hand-back** ships the *problems
> TFactory found* back *to* AIFactory for a fix. Together they close the
> AIFactory ↔ TFactory loop: **handover → test → (fail) → hand-back → AIFactory
> QA Fixer → re-test → done** (epic
> [#182](https://github.com/olafkfreund/TFactory/issues/182)).

This guide is the operator walkthrough. The data path is built end to end; the
*live* send + fixer run needs both apps running (see [End-to-end](#end-to-end)).

## The flow

```
TFactory task reaches a terminal status (triaged) with failing tests
   │
   ├─ Triager completion hook prepares findings/handback_request.{md,json}   (auto, default ON)
   │
   ▼
/handback-to-aifactory  →  preview  →  send (confirm)
   │                                     │
   │                          POST /api/tasks/{id}/apply-correction
   ▼                                     ▼
AIFactory writes QA_FIX_REQUEST.md onto the original spec → runs the QA Fixer
   │
   ▼
re-test (task_rerun) — bounded by /tfactory-fixloop → passed | stuck
```

**Automatic (event-driven) variant — epic #182.** Instead of polling with
`/tfactory-fixloop`, TFactory can *listen* for AIFactory's "fix done": when the
QA Fixer finishes, AIFactory POSTs to TFactory's inbound webhook, which applies
the bounded-loop guard and re-fires the pipeline itself — no human, no polling.

```
AIFactory QA Fixer done ─POST /api/handback/aifactory-complete─► TFactory web-server
                          (X-TFactory-Handback-Token)               │
                                                                    ▼
                              decide_loop → retest | stuck | passed
                              retest → task_rerun (full pipeline)
```

Nothing is assembled by hand: when the Triager goes terminal with failures, its
completion hook ([#185](https://github.com/olafkfreund/TFactory/issues/185))
builds the correction request and writes it to the workspace. **Preparing is
default ON; sending is opt-in.**

## What gets written

In the TFactory workspace (`~/.tfactory/workspaces/<project>/specs/<spec>/`):

| File | Who writes it | What it is |
|---|---|---|
| `findings/handback_request.md` | Triager hook (#185) | the `QA_FIX_REQUEST.md`-shaped payload AIFactory's QA Fixer reads |
| `findings/handback_request.json` | Triager hook | envelope: `aifactory_task_id`, failing tests, source, `dry_run` |
| `context/source.json` → `aifactory{}` + `correction_cycle` | snapshotter (#183) at handover | the hand-back target + loop state |

On the AIFactory side, the receiver writes `QA_FIX_REQUEST.md` into
`<project>/.aifactory/specs/<spec_id>/` and runs the QA Fixer.

## Operator paths

### A. The skill (recommended)

```
/handback-to-aifactory <task_id>
```

It reads the prepared request, previews the target spec + failing tests, and on
your confirmation sends via the AIFactory MCP tool `task_apply_correction`
(`confirm=false` preview → `confirm=true`).

### B. The local CLI (no AIFactory MCP needed)

```bash
cd apps/backend
python -m agents.handback <spec_dir>          # prepare + preview (no send)
python -m agents.handback <spec_dir> --send    # actually POST to AIFactory
```

Dry-run by default; `--send` is the explicit opt-in. It POSTs to the `api_url`
recorded in `source.json` (default `http://localhost:3101`).

### C. Hands-off, bounded loop

```
/loop 60s /tfactory-fixloop <task_id>
```

One bounded cycle per interval — hand back, wait for the QA Fixer, re-test —
stopping at **passed**, or **stuck** (the correction-cycle cap, default 2, or
the same tests still failing after a correction). The loop can never run away.

### D. Fully automatic (event-driven webhook, epic #182)

No polling and no operator step after the initial send: AIFactory calls TFactory
back when its fix is done, and TFactory auto-re-tests. Enable on the TFactory
**web-server**:

```bash
APP_INBOUND_HANDBACK_ENABLED=true
APP_INBOUND_HANDBACK_SECRET=<shared-secret>   # AIFactory sends this in the header
```

TFactory's outbound hand-back already tells AIFactory where to call back — the
`send` payload carries `tfactory_task_id` and `tfactory_callback_url`
(`<TFACTORY_SELF_API_URL>/api/handback/aifactory-complete`).

**Endpoint contract** — AIFactory's emitter POSTs:

```
POST /api/handback/aifactory-complete
Header: X-TFactory-Handback-Token: <APP_INBOUND_HANDBACK_SECRET>
Body:   { "tfactory_task_id": "<project_id>:<spec_id>", "status": "complete" }
```

Response `action` is one of:
- `retest` — under the cap and making progress → pipeline re-fired (`task_rerun`).
- `stuck` — cap reached or the same tests still fail → task flagged for a human.
- `passed` — no failing tests remain.
- `already_running` — a run is already in flight (idempotent; no double-fire).

The endpoint is **off** unless `APP_INBOUND_HANDBACK_ENABLED=true`, and rejects
any call without the matching secret (401). Same `TFACTORY_HANDBACK_MAX_CYCLES`
bound as the skill — the loop can never run away.

## Environment flags

| Flag | Default | Effect |
|---|---|---|
| `TFACTORY_HANDBACK_PREPARE` | **ON** | Triager hook builds + writes the artifact on a failing run. Set falsy to disable. |
| `TFACTORY_HANDBACK_SEND` | **OFF** | the hook also POSTs to AIFactory (opt-in; mirrors `TFACTORY_TRIAGER_GIT_WRITE`). |
| `TFACTORY_AIFACTORY_API_URL` | `http://localhost:3101` | AIFactory web-server base URL for the send. |
| `TFACTORY_HANDBACK_MAX_CYCLES` | `2` | correction-cycle cap before the loop declares `stuck`. |
| `TFACTORY_SELF_API_URL` | `http://localhost:3103` | this TFactory's base URL, used to build the `tfactory_callback_url` AIFactory calls back. |
| `APP_INBOUND_HANDBACK_ENABLED` | **OFF** | enable the inbound `/api/handback/aifactory-complete` webhook (web-server). |
| `APP_INBOUND_HANDBACK_SECRET` | — | shared secret the webhook requires in `X-TFactory-Handback-Token`. |

Per the **no-automatic-pushes** policy, the *send* is always either operator-
confirmed (skill) or an explicit `--send`/opt-in flag. Preparing the artifact is
local and side-effect-free.

## End-to-end

A real round-trip needs **both apps running** + LLM creds + Docker:

1. AIFactory web-server up on **port 3101** (`cd apps/web-server && python -m server.main`), with its LLM provider configured (the QA Fixer runs a real model).
2. The AIFactory receiver present — issue
   [`olafkfreund/AIFactory#317`](https://github.com/olafkfreund/AIFactory/issues/317):
   `POST /api/tasks/{task_id}/apply-correction` + MCP `task_apply_correction`.
3. From TFactory: `/handover-to-tfactory` → let the pipeline run → on a failing
   `triaged`, `/handback-to-aifactory` (or `/tfactory-fixloop`).
4. AIFactory's QA Fixer applies the fix on the original spec; `task_rerun`
   re-tests until green or the cap.

The unit suites (`tests/test_handback_*.py`, AIFactory `tests/test_qa_correction.py`)
cover everything *except* the live fixer run, which is this end-to-end step.

## See also

- `guides/handover-to-tfactory-skill.md` — the forward direction.
- `guides/e2e-smoke.md` — the operator-facing pipeline walkthrough.
- `docs/plans/2026-06-03-aifactory-tfactory-handback-design.md` — the design.

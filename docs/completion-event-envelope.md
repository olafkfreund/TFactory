# Completion-event envelope (v1)

> Part of the Factory **PARR-spine** epic (`olafkfreund/Factory`). Issue #198.

The connective tissue that lets the Factory products cooperate includes a
**normalized completion-event envelope** emitted by each service
(AIFactory · PFactory · TFactory) when a unit of work reaches a terminal
state. A single watcher (CFactory) can then consume one schema across all of
them. This document is TFactory's emission and the proposed **v1 contract**
for the other services to match.

## Correlation key

The spine correlation key is the **GitHub issue number** threaded end-to-end
(`correlation_id`). TFactory reads it from `status.json` or
`context/source.json` (`issue_number` / `correlation_id`); it is `null` until a
run carries one (populated by the PFactory pickup contract, epic #193).

## Schema

```jsonc
{
  "schema_version": "1.0",       // bump on any breaking field change
  "event": "completion",         // event type
  "service": "tfactory",         // emitting service: aifactory | pfactory | tfactory
  "correlation_id": 412,         // GitHub issue # (int) | null — the spine key

  "task_id": "001-pricing",      // emitting service's task id
  "project_id": "demo",          // project / repo identifier
  "spec_id": "001-pricing",      // spec id (TFactory) | null

  "status": "triaged",           // service-native terminal status (verbatim)
  "outcome": "success",          // normalized coarse outcome (see below)
  "phase": "triager_complete",   // service-native terminal phase

  "repo": "owner/name",          // GitHub slug | null
  "branch": "feat/x",            // feature branch | null
  "pr_number": 88,               // PR number (int) | null

  "result": {                    // service-specific summary; keys optional
    "committed_count": 3,
    "flagged_count": 1,
    "rejected_count": 2,
    "verdicts_count": 6,
    "dedup_collision_count": 0
  },

  "emitted_at": "2026-06-04T16:30:00+00:00",  // when this event was emitted
  "updated_at": "2026-06-04T16:29:58+00:00"   // status.json last-write time
}
```

The flat fields `task_id`, `project_id`, `status`, `phase`, `updated_at` are
retained from the original `#85` payload for backward-compatibility; existing
consumers keep working while new consumers read the normalized header.

### `outcome` mapping (normalized across services)

| outcome | meaning | TFactory terminal status |
|---|---|---|
| `success` | work completed with usable results | `triaged` |
| `empty` | completed, but nothing actionable produced | `triaged_empty` |
| `failure` | terminated without usable results | `triager_failed` (+ any `*_failed` / `stuck`) |

## Channels

Both are **opt-in** and **best-effort** — a missing/failing target never
affects the pipeline (consistent with the no-automatic-side-effects policy).
The same envelope is sent on both.

| Channel | Enable | Behaviour |
|---|---|---|
| Webhook | `TFACTORY_COMPLETION_WEBHOOK=<url>` | `POST` the envelope as JSON (timeout `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT`, default 5s) |
| Sentinel | `TFACTORY_COMPLETION_SENTINEL=1` | write `findings/COMPLETED.json` a same-host watcher can `stat` |

## Port map (PARR spine)

TFactory's default web-server port is **3103** (was 3102, which now belongs to
PFactory). Canonical local map: AIFactory `3101` · PFactory `3102` ·
TFactory `3103` · CFactory `3110/3111`.

## Implementation

`apps/backend/agents/triager.py` — `_build_completion_envelope()` builds the
envelope; `_notify_completion()` emits it on terminal status. Tests:
`tests/test_triager_completion_webhook.py`.

---
layout: default
title: Completion Event
permalink: /completion-event-envelope/
nav_order: 7.5
---

# Completion-event envelope

> Conforms to **[RFC-0001](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md)**
> — the canonical shared correlation-key + completion-event schema (Factory
> PARR-spine epic #1). TFactory emits this on terminal Triager status (#198/#211).

When a unit of work reaches a terminal state, TFactory emits a normalized
completion event so the watcher (CFactory) consumes one schema across all
services (AIFactory · PFactory · TFactory).

## Correlation key

Per RFC-0001 §2, the shared key is **`correlation_key`** — the GitHub issue
number rendered as a string, with a synthetic **`tf-<spec_id>`** fallback so it
is **never null**. TFactory reads the issue number from `status.json` or
`context/source.json` (`issue_number` / `correlation_id`, populated by the
PFactory pickup contract, epic #193); absent one, it falls back to the synthetic
key. The legacy int field `correlation_id` is retained as a backward-compat alias.

## Schema

The six RFC-0001 core fields, plus the optional `correlation` chain block, plus
additive TFactory detail (RFC §7 permits extra fields):

```jsonc
{
  // RFC-0001 core (required)
  "correlation_key": "412",      // issue# as string | "tf-<spec_id>" — never null
  "service": "tfactory",         // aifactory | pfactory | tfactory
  "task_id": "001-pricing",      // emitting service's task id
  "status": "triaged",           // service-native terminal status (verbatim)
  "phase": "test",               // pipeline phase (falls back to "test")
  "updated_at": "2026-06-04T16:29:58+00:00",

  // RFC-0001 §4 optional chain block (upstream/downstream links)
  "correlation": {
    "issue_number": 412,         // int | null
    "spec_id": "001-pricing",
    "branch": "feat/x",
    "pr_number": 88
  },

  // Additive TFactory detail (+ #85/#198 backward-compat fields)
  "schema_version": "1.0",
  "event": "completion",
  "correlation_id": 412,         // legacy alias of correlation_key (int | null)
  "project_id": "demo",
  "spec_id": "001-pricing",
  "outcome": "success",          // normalized coarse outcome (see below)
  "repo": "owner/name",
  "branch": "feat/x",
  "pr_number": 88,
  "result": { "committed_count": 3, "flagged_count": 1, "rejected_count": 2,
              "verdicts_count": 6, "dedup_collision_count": 0 },
  "emitted_at": "2026-06-04T16:30:00+00:00"
}
```

Consumers that only need the spine read the six RFC core fields + `correlation`;
the additive fields are ignored by RFC-conformant consumers (RFC §7).

### v1.2 — CloudEvents alignment + idempotency + trace context (#282)

`schema_version` is now **`1.2`**. The following ride **additively** alongside
everything above (nothing removed — parity with AIFactory's #466 envelope, and
validated by `apps/backend/contracts/completion-event.schema.json`):

```jsonc
{
  "id": "9f1c…-uuid4",                 // per-event idempotency key — consumers
                                       // dedup on this; stable across #281 relay
                                       // re-delivery (the persisted row is resent)
  "specversion": "1.0",                // CloudEvents core
  "source": "/tfactory",               // override: TFACTORY_EVENT_SOURCE
  "type": "io.factory.tfactory.completion",
  "time": "2026-06-04T16:29:58+00:00", // = updated_at (occurrence time)
  "traceparent": "00-<32hex>-<16hex>-01" // W3C trace context (OpenTelemetry)
}
```

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

TFactory's default web-server port is **3103**. Canonical local map (see the
[Factory port map](https://github.com/olafkfreund/Factory/blob/main/docs/dev/local-ports-and-run-all.md)):
AIFactory `3101` · TFactory `3103` · CFactory `3110/3111` · PFactory `3114/3115`
(PFactory moved to its own pair, freeing `3102`).

## Implementation

`apps/backend/agents/triager.py` — `_build_completion_envelope()` builds the
envelope; `_notify_completion()` emits it on terminal status. Tests:
`tests/test_triager_completion_webhook.py`.

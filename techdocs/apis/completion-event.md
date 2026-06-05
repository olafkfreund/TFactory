# Completion-event envelope

When a task reaches a **terminal status** (`triaged` / `triaged_empty` /
`triager_failed` / `stuck`), the Triager emits a completion event that conforms to
**[RFC-0001](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0001-correlation-key-and-completion-event.md)**
— the canonical shared correlation-key + completion-event schema across the Factory
spine (AIFactory · TFactory · PFactory · CFactory). This lets the watcher (CFactory)
consume one schema for every service without polling.

- **Conforms to:** RFC-0001 (Factory-wide contract)
- **Source spec:** `docs/completion-event-envelope.md`
- **Backstage AsyncAPI:**
  [`techdocs/specs/tfactory-completion-event.asyncapi.yaml`](https://github.com/olafkfreund/TFactory/blob/main/techdocs/specs/tfactory-completion-event.asyncapi.yaml)
- **Implementation:** `agents/triager.py` (`_build_completion_envelope()` + `_notify_completion()`, `_correlation_key` helper)

Both delivery channels are **opt-in and best-effort** — a failing target never breaks
the pipeline.

| Channel | Enable with | Behaviour |
|---------|-------------|-----------|
| Webhook | `TFACTORY_COMPLETION_WEBHOOK=<url>` | POSTs the envelope as JSON (timeout `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT`, default 5s) |
| Sentinel | `TFACTORY_COMPLETION_SENTINEL=1` | writes `findings/COMPLETED.json` a same-host watcher can `stat` |

## The correlation key (RFC-0001 §2)

The shared join key is **`correlation_key`** — the GitHub issue number rendered as a
**string**, with a synthetic **`tf-<spec_id>`** fallback so it is **never null**.
TFactory reads the issue number from `status.json` / `context/source.json`
(`issue_number` / `correlation_id`). The legacy integer field `correlation_id` is
retained as a backward-compat alias.

## Envelope shape

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

  // Additive TFactory detail (+ #85/#198 backward-compat fields, RFC §7)
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

RFC-conformant consumers read the six core fields + `correlation`; the additive
fields are ignored (RFC §7).

## Outcome mapping

| `outcome` | meaning | TFactory `status` |
|-----------|---------|-------------------|
| `success` | completed with usable results | `triaged` |
| `empty` | completed but nothing actionable | `triaged_empty` |
| `failure` | terminated without usable results | `triager_failed` / `*_failed` / `stuck` |

> Now ingestable by the CFactory collector (`olafkfreund/CFactory#24`). Port map:
> TFactory `3103`, PFactory `3114/3115`.

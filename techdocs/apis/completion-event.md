# Completion-event envelope

When a task reaches a **terminal status** (`triaged` / `triaged_empty` /
`triager_failed` / `stuck`), the Triager emits a normalized **v1 completion-event
envelope** — the cross-service shape of the Factory PARR spine. This lets
`/tfactory-watch` and downstream services (AIFactory, CFactory) react without
polling.

- **Schema version:** `1.0`
- **Source spec:** `docs/completion-event-envelope.md`
- **Backstage AsyncAPI:**
  [`techdocs/specs/tfactory-completion-event.asyncapi.yaml`](https://github.com/olafkfreund/TFactory/blob/main/techdocs/specs/tfactory-completion-event.asyncapi.yaml)
- **Implementation:** `agents/triager.py::_build_completion_envelope()` + `_notify_completion()`

Both channels are **opt-in and best-effort** — a failing target never breaks the
pipeline.

| Channel | Enable with | Behaviour |
|---------|-------------|-----------|
| Webhook | `TFACTORY_COMPLETION_WEBHOOK=<url>` | POSTs the envelope as JSON (timeout `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT`, default 5s) |
| Sentinel | `TFACTORY_COMPLETION_SENTINEL=1` | writes `findings/COMPLETED.json` a same-host watcher can `stat` |

## Envelope shape

```json
{
  "schema_version": "1.0",
  "event": "completion",
  "service": "tfactory",
  "correlation_id": 412,

  "task_id": "001-pricing",
  "project_id": "demo",
  "spec_id": "001-pricing",

  "status": "triaged",
  "outcome": "success",
  "phase": "triager_complete",

  "repo": "owner/name",
  "branch": "feat/x",
  "pr_number": 88,

  "result": {
    "committed_count": 3,
    "flagged_count": 1,
    "rejected_count": 2,
    "verdicts_count": 6,
    "dedup_collision_count": 0
  },

  "emitted_at": "2026-06-04T16:30:00+00:00",
  "updated_at": "2026-06-04T16:29:58+00:00"
}
```

`correlation_id` is the **GitHub issue number** (the PARR-spine join key), or `null`.

## Outcome mapping

| `outcome` | meaning | TFactory `status` |
|-----------|---------|-------------------|
| `success` | completed with usable results | `triaged` |
| `empty` | completed but nothing actionable | `triaged_empty` |
| `failure` | terminated without usable results | `triager_failed` / `*_failed` / `stuck` |

The envelope carries both the **normalized** fields (`schema_version`, `event`,
`service`, `correlation_id`, `outcome`) and the **legacy flat fields** so older
consumers keep working.

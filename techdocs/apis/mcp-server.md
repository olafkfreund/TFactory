# MCP server

TFactory ships a **Model Context Protocol** server so external agents (Claude Code,
Cursor, Continue.dev) can drive the pipeline as a tool surface.

- **Server name:** `tfactory`
- **Version:** `1.0.0`
- **Transport:** stdio (JSON-RPC 2.0); HTTP+SSE via the web-server's `/api/mcp-remote`
  proxy (`TFACTORY_MCP_REMOTE_ENABLED=true`)
- **Entry point:** `python -m apps.backend.mcp_server.tfactory_server [--spec-dir <path>]`
- **Source:** `apps/backend/mcp_server/tfactory_server.py`

The machine-readable tool catalog is in
[`techdocs/specs/tfactory-mcp.md`](https://github.com/olafkfreund/TFactory/blob/main/techdocs/specs/tfactory-mcp.md)
(the definition rendered on the `tfactory-mcp` API entity).

## Task-control tools (`task_control.py`)

Registered **only** in the standalone MCP server — never inside in-process agent
sessions (this prevents an agent from recursively driving itself).

| Tool | Purpose |
|------|---------|
| `task_create_and_run` | Create a TFactory task from an AIFactory spec and optionally kick off the pipeline. |
| `task_status` | Get lifecycle state — status, phase, per-lane progress, timestamps. |
| `task_list` | List tasks (filter by `project_id`, `status`; max 50). |
| `task_rerun` | Re-execute a lane against an existing task. |
| `report_get` | Fetch a task's final triage report (`md` or `json`). |
| `project_list` | List registered AIFactory projects. |
| `project_create` | Register an AIFactory project for handover. |

## Spec-internal tools (`registry.create_all_tools`)

Available to the in-process agent (and the standalone server). These mutate the
current spec's workspace files:

| Group | Tools | Purpose |
|-------|-------|---------|
| Subtask | `update_subtask_status` | update a subtask's status in `test_plan.json` |
| Progress | `get_build_progress` | completed / pending / in-progress subtask counts |
| QA | `update_qa_status`, `record_gotcha`, `record_discovery` | QA tracking + memory hints |
| Memory | `record_memory`, `get_memory_context` | Graphiti / LadybugDB memory |

## Typical agent loop

```text
project_create        → register the repo (once)
task_create_and_run   → hand over a finished feature branch
task_status (poll)    → wait for terminal status (triaged / triaged_empty / *_failed)
report_get            → pull the ranked triage report
task_rerun            → (optional) re-run a single lane
```

In practice this is wrapped by the `/handover-to-tfactory` and `/tfactory-watch`
skills — `/tfactory-watch` reads `task_status` + the triage report file directly,
avoiding any polling of the REST layer.

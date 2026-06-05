# TFactory MCP — tool catalog

Machine-readable definition rendered on the `tfactory-mcp` Backstage API entity.

- **Server name:** `tfactory`
- **Version:** `1.0.0`
- **Protocol:** Model Context Protocol (JSON-RPC 2.0)
- **Transport:** stdio (default) · HTTP+SSE via `/api/mcp-remote` (opt-in)
- **Entry point:** `python -m apps.backend.mcp_server.tfactory_server [--spec-dir <path>]`
- **Source:** `apps/backend/mcp_server/tfactory_server.py`

## Tools

### Task-control (standalone server only)

| Tool | Purpose |
|------|---------|
| `task_create_and_run` | Create a TFactory task from an AIFactory spec; optionally start the Planner→…→Triager pipeline. |
| `task_status` | Return lifecycle state: status, phase, per-lane progress, timestamps. |
| `task_list` | List tasks; filter by `project_id` and/or `status` (max 50). |
| `task_rerun` | Re-execute a single lane against an existing task. |
| `report_get` | Fetch the final triage report (`format`: `md` or `json`). |
| `project_list` | List registered AIFactory projects. |
| `project_create` | Register an AIFactory project for handover. |

### Spec-internal (in-process agent + standalone)

| Tool | Purpose |
|------|---------|
| `update_subtask_status` | Update a subtask's status in `test_plan.json`. |
| `get_build_progress` | Completed / pending / in-progress subtask counts. |
| `update_qa_status` | Update QA verdict state. |
| `record_gotcha` | Persist a gotcha to memory. |
| `record_discovery` | Persist a discovery to memory. |
| `record_memory` | Write a memory entry (Graphiti / LadybugDB). |
| `get_memory_context` | Retrieve relevant memory context for a session. |

## Access control

- **stdio:** inherits the launching process; scoped to `--spec-dir`.
- **HTTP/SSE proxy** (`/api/mcp-remote`, `/api/mcp-stdio`): authenticated with
  scoped `acw_`-prefixed API keys; the legacy admin token also works.
- Task-control tools are intentionally **absent** from in-process agent sessions to
  prevent recursive self-driving.

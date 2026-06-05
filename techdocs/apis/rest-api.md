# Web REST API

The FastAPI backend (`apps/web-server/`) is the management surface for the portal
and any HTTP integration.

- **Title:** `TFactory Web API`
- **Description:** *Web API for TFactory — self-hosted AI task management + agent orchestration*
- **Version:** `0.5.0` (read from `apps/backend/__init__.py`)
- **Auth:** Bearer **JWT** (`BearerAuth` security scheme applied to all endpoints)
- **Interactive docs:** Swagger UI at `/docs`, ReDoc at `/redoc` — enabled only when
  `APP_DEBUG=true`
- **OpenAPI:** generated dynamically (`custom_openapi()` in `server/main.py`);
  ~**300 paths**. The committed snapshot is
  [`techdocs/specs/tfactory-web-api.openapi.json`](https://github.com/olafkfreund/TFactory/blob/main/techdocs/specs/tfactory-web-api.openapi.json)
  and is rendered by Backstage on the `tfactory-web-api` API entity.

!!! note "Regenerating the spec"
    The committed OpenAPI snapshot is produced from the running app:
    ```bash
    cd apps/web-server
    .venv/bin/python -c "import json; from server.main import app; \
      json.dump(app.openapi(), open('../../techdocs/specs/tfactory-web-api.openapi.json','w'), indent=2)"
    ```
    Re-run after adding or changing routes so Backstage stays in sync.

## Route groups (by router prefix)

| Prefix | Router | Purpose |
|--------|--------|---------|
| `/api/auth` | `auth_routes.py` | register / login / refresh / logout / me (JWT) |
| `/api/auth/oidc` | `oidc_routes.py` | OIDC login + callback (`APP_OIDC_ENABLED`) |
| `/api/orgs` | `organizations.py`, `audit.py` | organizations, members, audit log + export |
| `/api/keys` | `api_keys.py` | scoped API-key management |
| `/api/git-credentials` | `git_credentials.py` | encrypted Git credentials |
| `/api/test-credentials` | `test_target_credentials.py` | test-target login credentials (#107) |
| `/api/provider-runtimes` | `provider_runtimes.py` | LLM provider runtime config |
| `/api/llm` | `llm_endpoints.py` | models, providers, connection test |
| `/api/cloud` | `cloud.py` | cloud posture assessments (run / list / get) (#133) |
| `/api/visual-inspections` | `visual_inspection.py` | visual-regression baselines (#109) |
| `/api/projects` | `projects.py`, `auto_fix.py`, `mcp.py` | projects, init, worktrees, tasks, auto-fix, mcp-status |
| `/api/tasks` | `tasks.py`, `execution.py` | task logs, status, start / stop / recover, create-and-run |
| `/api/tfactory/tasks` | `tfactory_tasks.py` | TFactory task list / get / run |
| `/api/files` | `files.py` | list / read / write / search / diff / serve / discover |
| `/api/terminals` | `terminal.py` | PTY session create / input / close |
| `/api/email` | `email.py` | Gmail / Outlook OAuth + test |
| `/api/github` | `github.py` | GitHub status + verify |
| `/api/git`, `/api/ollama`, `/api/claude-code`, `/api/mcp`, `/api/updates` | `git.py` | git ops, Ollama mgmt, Claude Code, MCP, update checks |
| `/api/capabilities` | `capabilities.py` | feature discovery for the UI |
| `/api/memory` | `context.py` | Graphiti / memory DB config + test |
| `/api/logs` | `logs.py` | server log listing / fetch |
| `/api/skills` | `skills.py`, `tfactory_skills.py` | skills catalog |
| `/api/settings` | `settings.py`, `cli_accounts.py` | app settings + CLI account login/import |
| `/api/notifications` | `notifications.py` | notifications |
| `/api/health` | — | `{ "status": "healthy", "version": "<v>" }` |

> The committed OpenAPI document is authoritative for exact methods, paths,
> parameters and schemas. The table above is a navigational index.

## WebSocket endpoints

Real-time channels under `apps/web-server/server/websockets/`:

| Channel | Purpose |
|---------|---------|
| `…/{project_id}/logs` | stream task logs |
| `…/{project_id}/progress` | stream build-progress events |
| `…/{project_id}/events` | stream stage-lifecycle events |
| `…/{session_id}/terminal` | interactive terminal session |

## MCP proxies on the web-server

| Mount | Transport | Notes |
|-------|-----------|-------|
| `/api/mcp-stdio` | stdio bridge | always mounted; scoped via `acw_`-keyed API keys |
| `/api/mcp-remote` | HTTP + SSE | opt-in `TFACTORY_MCP_REMOTE_ENABLED=true`; for Cursor / Continue.dev |

See the [MCP server](mcp-server.md) page for the tool surface itself.

## Response conventions

- The frontend `api-client.ts` wraps responses as `{ success: true, data: <body> }`;
  **backend endpoints return raw objects**, not pre-wrapped.
- Error responses return `{ success: false, error: "message" }`.

# Configuration & operations

## Running TFactory

### CLI

```bash
cd apps/backend
python run.py --spec 001        # autonomous build
python run.py --list            # list specs
python run.py --spec 001 --review|--merge|--discard
python run.py --spec 001 --qa   # run QA manually
```

### Web interface

```bash
# Backend (port 3103)
cd apps/web-server && source .venv/bin/activate && python -m server.main
# Frontend (port 3100)
cd apps/frontend-web && npm run dev
```

## Pipeline auto-fire flags

Each stage fires the next on success, gated by env (default **ON** in production,
**OFF** in tests):

| Var | Stage |
|-----|-------|
| `TFACTORY_AUTO_PLAN` | run the Planner |
| `TFACTORY_AUTO_GENERATE` | run Gen-Functional |
| `TFACTORY_AUTO_EVALUATE` | run the Evaluator |
| `TFACTORY_AUTO_TRIAGE` | run the Triager |

## Triager side-effects (dry-run by default)

| Var | Effect |
|-----|--------|
| `TFACTORY_TRIAGER_GIT_WRITE=1` | commit tests to the feature branch |
| `TFACTORY_TRIAGER_PR_COMMENT=1` | post the triage PR comment |

## Completion-event notifications (opt-in)

| Var | Effect |
|-----|--------|
| `TFACTORY_COMPLETION_WEBHOOK=<url>` | POST the [envelope](apis/completion-event.md) |
| `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT` | webhook timeout (default 5s) |
| `TFACTORY_COMPLETION_SENTINEL=1` | write `findings/COMPLETED.json` |

## AIFactory handback (epic #182)

| Var | Default | Effect |
|-----|---------|--------|
| `TFACTORY_HANDBACK_PREPARE` | ON | build + write `findings/handback_request.{md,json}` |
| `TFACTORY_HANDBACK_SEND=1` | OFF | also POST the correction to AIFactory |
| `TFACTORY_AIFACTORY_API_URL` | `http://localhost:3101` | AIFactory web-server |
| `TFACTORY_HANDBACK_MAX_CYCLES` | 2 | correction-cycle cap → `stuck` |

## Workspace & storage

| Var | Purpose |
|-----|---------|
| `TFACTORY_WORKSPACE_ROOT` | override `~/.tfactory/workspaces` |

- `.tfactory/specs/` — per-project data (gitignored).
- `~/.tfactory/` — web-UI data (projects, settings, token, workspaces).

## Web-server settings (`apps/web-server/.env`)

| Var | Purpose |
|-----|---------|
| `APP_HOST` / `APP_PORT` | listen address (default `0.0.0.0:3103`) |
| `APP_DEBUG` | enable Swagger `/docs` + ReDoc `/redoc` |
| `APP_API_TOKEN` | fixed token (auto-generated to `~/.tfactory/.token` if unset) |
| `APP_OIDC_ENABLED` | enable OIDC login |
| `SSL_ENABLED` / `SSL_CERTFILE` / `SSL_KEYFILE` | TLS |
| `TFACTORY_MCP_REMOTE_ENABLED` | mount the HTTP/SSE MCP proxy at `/api/mcp-remote` |

## MCP remote server

For non-Claude agents (Cursor, Continue.dev): see `guides/REMOTE_MCP_SERVER.md`.

## Observability

- Structured JSON logs via `structlog` (`~/.tfactory/logs/`: server.log, errors.log,
  agent.log).
- Prometheus metrics via `prometheus-fastapi-instrumentator`.

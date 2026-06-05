# APIs overview

TFactory exposes three distinct API surfaces, each registered as a Backstage `API`
entity in [`catalog-info.yaml`](https://github.com/olafkfreund/TFactory/blob/main/catalog-info.yaml).

| API | Kind | Transport | Consumers | Spec |
|-----|------|-----------|-----------|------|
| [**Web REST API**](rest-api.md) | `openapi` | HTTPS REST + WebSocket | the React portal, scripts, integrations | `techdocs/specs/tfactory-web-api.openapi.json` |
| [**MCP server**](mcp-server.md) | `mcp` | JSON-RPC over stdio (+ HTTP/SSE) | Claude Code, Cursor, agents | `techdocs/specs/tfactory-mcp.md` |
| [**Completion-event envelope**](completion-event.md) | `asyncapi` | webhook POST / file sentinel | AIFactory, CFactory, watchers | `techdocs/specs/tfactory-completion-event.asyncapi.yaml` |

## Choosing a surface

- **Building a UI or integrating over HTTP?** Use the **Web REST API**. It is the
  full management surface (~300 routes): auth, projects, tasks, execution, files,
  terminal, cloud assessment, credentials, settings.
- **Driving TFactory from an AI agent?** Use the **MCP server**. It is the narrow
  control plane: create/run/inspect/re-run tasks and fetch reports.
- **Reacting to a run finishing?** Subscribe to the **completion-event envelope**
  (webhook) or stat the sentinel file — no polling required.

## Authentication summary

| Surface | Auth |
|---------|------|
| Web REST API | Bearer **JWT** (`BearerAuth`); bootstrap admin token in `~/.tfactory/.token`; optional OIDC |
| MCP (HTTP/SSE proxy) | scoped `acw_`-prefixed API keys (legacy admin token also works) |
| MCP (stdio) | inherits the host process; launched per-spec |
| Completion webhook | none by default — POST to a URL you control (add your own gateway auth) |

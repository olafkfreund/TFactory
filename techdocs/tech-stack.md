# Tech stack & choices

## Languages & runtimes

| Area | Choice | Why |
|------|--------|-----|
| Backend / pipeline | **Python 3.12+** | Rich AST tooling for the flake-lint + mutation probes; first-class Claude Agent SDK. |
| Web server | **FastAPI** + **Uvicorn** | Async, auto-generated OpenAPI, native WebSocket. |
| Frontend | **React 19 + TypeScript + Vite** | Browser-based portal, fast HMR, strict typing. |
| Test runners (under test) | **pytest · Jest · Playwright** | The v0.2 polyglot lane spine (unit / api / integration / browser / mutation). |

## Backend dependencies (`apps/backend/requirements.txt`)

- **Agent SDK:** `claude-agent-sdk>=0.1.16` — primary LLM interface.
- **Providers:** `anthropic>=0.84.0`, `google-generativeai>=0.8.0`, plus
  OpenAI-compatible + Ollama (see [LLM providers](providers.md)).
- **Memory:** `real_ladybug>=0.13.0` (embedded graph DB, no Docker) + `graphiti-core>=0.5.0`.
- **Code analysis:** `tree-sitter>=0.21.0` + language grammars.
- **Config/CLI:** `click`, `PyYAML`, `pydantic>=2`, `python-dotenv`.

## Web-server dependencies (`apps/web-server/requirements.txt`)

- **Web:** `fastapi>=0.109`, `uvicorn[standard]>=0.27`.
- **DB:** `sqlalchemy[asyncio]>=2`, `alembic`, `aiosqlite` (dev), `asyncpg` (prod Postgres).
- **Auth:** `python-jose[cryptography]`, `passlib[bcrypt]`, `authlib` (OIDC).
- **Secret KMS:** `boto3` (AWS KMS), `hvac` (Vault), `azure-keyvault-keys` +
  `azure-identity`, `google-cloud-kms` + `google-crc32c`.
- **Observability:** `structlog` (JSON logs), `prometheus-fastapi-instrumentator`.
- **Terminal:** `ptyprocess`. **HTTP:** `httpx`.

## Key architectural choices

- **Generator ≠ validator.** The Evaluator is structurally separate from
  Gen-Functional — research-mandated so a model never self-certifies its own tests.
- **Five signals, not coverage.** Quality is gated on coverage delta · stability ·
  mutation · flake-lint · semantic relevance — coverage alone is rejected as a proxy.
- **Sandboxed execution.** Tests run `--network=none --read-only` in Docker.
- **No automatic pushes.** Every Git/PR side-effect is dry-run by default.
- **Provider-agnostic LLM.** Never call `anthropic.Anthropic()` directly; route
  through `core.client.create_client()` / `providers.factory.get_provider()`.
- **Embedded memory.** Graphiti over LadybugDB — no external graph DB to operate.
- **Polyglot via a framework registry.** A subtask's `(language, framework, lane)`
  resolves a descriptor (runtime image + prompt context block); mutation is routed
  per-language.

## Repositories & hosting

- **Source:** <https://github.com/olafkfreund/TFactory> (monorepo).
- **Branching:** feature work + PRs target **`dev`**; **`main`** only receives
  promotion merges from `dev`.
- **Releases:** `scripts/bump-version.js` + GitHub Actions on merge to `main`
  (tag → multi-platform build → release). See `RELEASE.md`.

# TFactory

**Autonomous test generation + execution platform — sister project to [AIFactory](https://github.com/olafkfreund/AIFactory).**

TFactory ingests a finished AIFactory spec, generates feature + security tests
aligned to its acceptance criteria, runs them in a sandbox, evaluates quality,
commits the tests to the feature branch, and posts a coverage + findings report
to the PR — autonomously.

> Status: **v0.2.0 released · 16 of 16 v0.2 tasks delivered ·
> Browser + API + Integration lanes active · test evidence capture live.**
> See [Progress](https://olafkfreund.github.io/TFactory/progress/) for the
> per-task log.

## Quickstart (NixOS / flake-based)

```bash
# One-command dev environment via the flake:
nix develop

# (inside the shell)
tfactory-minimal-venv   # creates apps/backend/.venv with just pytest+pytest-asyncio
tfactory-test           # runs the non-SDK backend suite (~10s)

# For the full backend SDK install (graphiti, claude-agent-sdk, etc.):
bootstrap-venv
```

The dev shell brings in **Python 3.13, Node 22, uv, git, gh, just,
ripgrep, jq, docker-client** plus four shell functions: `bootstrap-venv`,
`tfactory-minimal-venv`, `tfactory-test`, `verify-fork`.

For auto-loading via `direnv`:

```bash
nix profile install nixpkgs#nix-direnv
direnv allow
```

Non-Nix users can fall back to `npm run install:backend` (per the
[Quickstart](https://olafkfreund.github.io/TFactory/) on Pages) — the
Nix path just makes setup deterministic.

> **Note for non-Nix npm users:** the nix devShell sets `NODE_ENV=production`,
> which makes `npm install` skip devDependencies (including vitest). If
> you're inside `nix develop` and running `npm install` in `apps/frontend-web/`,
> first `unset NODE_ENV`. Captured in detail in `guides/e2e-smoke.md`.

## Running the portal

```bash
# Backend (FastAPI on :3102)
cd apps/web-server
source .venv/bin/activate    # if you have a per-app venv
python -m server.main

# Frontend (Vite dev server on :3100)
cd apps/frontend-web
npm install                  # unset NODE_ENV first if inside nix develop
npm run dev
```

Then visit **http://localhost:3100** for the TFactory portal.

The portal exposes a `/tfactory` view powered by the components under
`apps/frontend-web/src/components/tfactory/`:

- **TFactoryTaskList** — workspace list with status badges
- **TFactoryTaskDetail** — tabs for Status / Lanes / Verdicts / Report / Logs
- **LaneStatusGrid** — Functional lit; SAST/DAST/Fuzz/Mutation as Phase 2-5 placeholders
- **TFactoryLogViewer** — WebSocket live tail (one snapshot per connect at MVP)

## End-to-end smoke

Once you have a real AIFactory project + a Claude API key + Docker:

```bash
# List the 9 verification scenarios
scripts/e2e-smoke.sh --list

# Dry-run (no env, no LLM calls) — sanity check the runner itself
scripts/e2e-smoke.sh --dry-run --all

# Real run
export ANTHROPIC_API_KEY=sk-ant-...
export TFACTORY_AIFACTORY_ROOT=$HOME/Source/GitHub/MyApp
export TFACTORY_AIFACTORY_BRANCH=feature/...
scripts/e2e-smoke.sh --all
```

Full walkthrough — including the 3 manual scenarios (mutation,
hallucination guard, docker-down) — in **`guides/e2e-smoke.md`**.

## Tests

| Suite | What | Count | Time |
|---|---|---|---|
| Backend non-SDK (`tests/test_*.py`) | Pure-Python primitives + agent loops with mocked SDK | **531** | ~9s |
| Frontend (`apps/frontend-web/src/**/*.test.tsx`) | vitest + React Testing Library | **112** | ~1.5s |
| End-to-end smoke (`scripts/e2e-smoke.sh`) | Real LLM + Docker + git + gh — **manual** | 9 scenarios | — |

CI runs the first two on every commit; the third is operator-driven.

```bash
# Backend
PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest -q tests/

# Frontend (under nix devShell, unset NODE_ENV first)
cd apps/frontend-web && ../../node_modules/.bin/vitest run

# Fork-hygiene check (every stray AIFactory reference is allowlisted explicitly)
scripts/verify-fork.sh --no-import
```

## Docs

Full project documentation is published as a GitHub Pages site:
**https://olafkfreund.github.io/TFactory/**

Direct links:

- [Design Plan](https://olafkfreund.github.io/TFactory/design-plan/) — full rationale, 10 locked decisions, landscape research, risk register
- [Spec](https://olafkfreund.github.io/TFactory/spec/) — Agent OS spec
- [Technical Spec](https://olafkfreund.github.io/TFactory/technical-spec/) — architecture detail
- [Test Coverage Spec](https://olafkfreund.github.io/TFactory/tests/) — TDD plan
- [Task Breakdown](https://olafkfreund.github.io/TFactory/tasks/) — 12 tasks with dependency graph

In-repo guides (`guides/`):

- **`guides/e2e-smoke.md`** — operator guide for the 9 verification scenarios
- **`guides/planner-manual-smoke.md`** — Planner-only sibling smoke
- **`guides/HANDOVER_WORKFLOW.md`** — how to trigger TFactory from a live Claude Code session
- **`guides/CLAUDE_CODE_MCP_TOOLS.md`** — driving TFactory tasks from the MCP control plane

## Project tracking

- **Epic + sub-issues:** https://github.com/olafkfreund/TFactory/issues
- **Discussions / questions:** open an issue with the `question` label

## High-level architecture

```
AIFactory finished branch  ─►  /handover-to-tfactory  ─►  TFactory MCP
                                                              │
                                                              ▼
                                                          Planner
                                                              │
                              ┌──────────┬─────────┬──────────┼──────────┐
                              ▼          ▼         ▼          ▼          ▼
                          Gen-Func   Gen-SAST  Gen-DAST   Gen-Mut     (more)
                              └──────────┴────┬────┴──────────┴──────────┘
                                              ▼
                                          Executor  (Docker per task)
                                              ▼
                                          Evaluator  (separate agent)
                                              ▼
                                          Triager   ─►  git commit + PR comment
```

Six agents (Planner / per-lane Generators / Executor / Evaluator / Triager),
four lanes (functional active at MVP), tiered sandbox (native for static,
Docker for runtime), spec-aware handover from AIFactory.

The four-stage chain auto-advances via `TFACTORY_AUTO_*` env vars; each
stage writes its outputs to `~/.tfactory/workspaces/{project}/specs/{spec}/`
and forwards via a fire-and-forget scheduler. See `apps/backend/agents/`
for each agent's implementation.

## Status by lane

| Lane | Status | Phase |
|---|---|---|
| Functional (pytest) | Active at MVP | 1 |
| SAST + deps + secrets | Planned | 2 |
| DAST | Planned | 3 |
| Fuzz | Planned | 4 |
| Mutation testing (mutmut) | Planned | 5 |
| Go / Rust / Ruby language ramp | Planned | 6+ |

## License

[MIT OR GPL-3.0](LICENSE).

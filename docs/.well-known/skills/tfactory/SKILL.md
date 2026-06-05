---
name: tfactory
description: >-
  Teach an AI coding assistant how to work in the TFactory codebase — the
  autonomous test-generation + execution platform. Covers the 4-agent pipeline,
  the LLM-provider abstraction (never call anthropic directly), the workspace
  layout, branching model, how to run the suite, and the non-negotiable
  conventions (i18n, no-automatic-pushes, dry-run side-effects).
license: MIT OR GPL-3.0
homepage: https://github.com/olafkfreund/TFactory
metadata:
  version: 0.5.0
  catalog-entity: system:default/tfactory
---

# Working in TFactory

TFactory is an **autonomous test-generation + execution platform**. It receives a
finished feature on a branch, generates tests aligned to the acceptance criteria,
runs them in a Docker sandbox, scores them with a 5-signal verdict pipeline
(coverage delta · 3× stability · mutate-and-check · flake-lint promotion · LLM
semantic relevance), and emits a ranked triage report ready to commit + post to
the PR.

Use this skill when making changes anywhere under `apps/backend/`,
`apps/web-server/`, or `apps/frontend-web/`.

## The pipeline (4 agents)

```
Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
 (#6)        (#7)             (#5)         (#8)         (#9)
```

Each agent lives under `apps/backend/agents/`:

| Agent          | File                              | Role |
|----------------|-----------------------------------|------|
| Planner        | `planner.py`                      | Emits lane-tagged `test_plan.json` (one phase per AC). |
| Gen-Functional | `gen_functional.py`               | Writes one test file per subtask, guarded by `preflight_static.py` + `flake_risk_lint.py`. |
| Executor       | `tools/runners/docker_runner.py`  | Runs pytest in a `--network=none --read-only` container. No LLM. |
| Evaluator      | `evaluator.py`                    | Builds an `EvaluatorSignals` bundle → `findings/verdicts.json`. Structurally separate from Gen-Functional (no self-validation). |
| Triager        | `triager.py`                      | Dedup + rank → `findings/triage_report.{md,json}`; emits the completion-event. |

Stages auto-fire the next via `schedule_<next>(spec_dir, project_dir)`, gated by
`TFACTORY_AUTO_{PLAN,GENERATE,EVALUATE,TRIAGE}` (ON in prod, OFF in tests).

## Hard rules (do not break these)

1. **Never call `anthropic.Anthropic()` directly.** Route Claude through
   `core.client.create_client()`; route other providers through
   `providers.factory.get_provider()`. Provider is inferred from the model
   string via `phase_config.infer_provider_from_model()`.
2. **No automatic pushes.** Triager git commit + PR comment default to
   **DRY-RUN**. Operators opt in with `TFACTORY_TRIAGER_GIT_WRITE=1` /
   `TFACTORY_TRIAGER_PR_COMMENT=1`. Handback to AIFactory is opt-in too.
3. **Branch from `dev`, not `main`.** `dev` is the working branch; `main` only
   receives `release:` promotion merges. PRs target `dev`. Commit with sign-off
   (`git commit -s`).
4. **Frontend text must use i18n keys.** No hardcoded strings in JSX/TSX — add
   keys to every locale (at minimum `en/*.json` and `fr/*.json`), namespaced
   `namespace:section.key`.
5. **No debug/analysis files in the repo root.** Use `/tmp/` or `guides/` (only
   if it's permanent, shared documentation).

## Where things live

```
apps/backend/      # ALL agent logic + the CLI
  core/            # client.py (SDK factory), security.py, auth.py
  agents/          # the 4 agents + primitives (coverage_delta, stability_runner, mutate_probe, ...)
  providers/       # multi-provider factory (Codex, Copilot, Gemini, Ollama, OpenAI-compatible)
  integrations/    # graphiti (memory), bmad, github, linear
  mcp_server/      # tfactory_server.py — task-control MCP tools
apps/web-server/   # FastAPI REST + WebSocket backend (~300 routes)
apps/frontend-web/ # React 19 + TS + Vite portal
techdocs/          # Backstage TechDocs source (mkdocs docs_dir)
docs/              # Jekyll GitHub Pages site (NOT the same as techdocs/)
guides/            # Operator/developer guides
```

Per-task workspace: `~/.tfactory/workspaces/<project>/specs/<spec>/`
(override with `TFACTORY_WORKSPACE_ROOT`) — holds `status.json`,
`test_plan.json`, `context/`, `tests/`, `findings/`, `logs/`.

## Running things

```bash
# Tests (use the venv pytest — it has the full backend + web-server deps)
apps/backend/.venv/bin/pytest tests/ -v
apps/backend/.venv/bin/pytest tests/ -m "not slow"     # skip slow

# Backend CLI
cd apps/backend && python run.py --spec 001            # run a build
python spec_runner.py --interactive                    # create a spec

# Web interface
cd apps/web-server && source .venv/bin/activate && python -m server.main   # :3103
cd apps/frontend-web && npm run dev                                        # :3100
```

The test suite never hits a real LLM — the SDK seams (`_resolve_*_client`,
`_invoke_session`) are mockable, and auto-fire env flags are pinned OFF.

## Authoritative references

- Architecture & conventions: repo-root `CLAUDE.md`
- Product source-of-truth: `.agent-os/product/` (mission, roadmap, decisions, pricing)
- TechDocs: `techdocs/` (built by Backstage from `mkdocs.yml`)
- Completion-event contract: `docs/completion-event-envelope.md`
- Backstage catalog entity: `system:default/tfactory` (`catalog-info.yaml`)

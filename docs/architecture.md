---
layout: default
title: Architecture
permalink: /architecture/
nav_order: 7
---

# Architecture

A walk through what's in this repo, where each concern lives, and how
the runtime pieces connect when a TFactory task fires. Everything below
reflects what's actually on `main` as of the last commit — see
[Progress]({{ '/progress/' | relative_url }}) for the live task status.

## Six-agent pipeline (full vision)

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

At MVP only the **functional** lane is lit. SAST / DAST / fuzz / mutation
land in phases 2-5.

## Lane status

| Lane | Phase | Tool (Python) | Tool (TypeScript, ph. 4) | Code today |
|---|---|---|---|---|
| **functional** | **1 (MVP)** | pytest + pytest-cov | vitest / jest | `tools/runners/docker_runner.py` |
| sast | 3 | semgrep + bandit | semgrep + eslint-sec | `lang_registry.py` placeholder |
| deps | 3 | pip-audit + OSV | npm audit + OSV | placeholder |
| secrets | 3 | gitleaks | gitleaks | placeholder |
| mutation | 2 | mutmut / cosmic-ray | stryker | placeholder |
| dast | 5 | OWASP ZAP | OWASP ZAP | placeholder |
| fuzz | 5 | atheris | jsfuzz / fast-check | placeholder |

The `_MVP_LIT_LANES = {"functional"}` set in
[`lane_dispatch.py`](https://github.com/olafkfreund/TFactory/blob/main/apps/backend/tools/runners/lane_dispatch.py)
is the single source of truth for which lanes have a real runner; gated
lanes raise `LaneNotImplementedError` with a phase pointer.

## Repository layout (depth 3)

```
TFactory/
├── apps/
│   ├── backend/                  # Python 3.12, Claude Agent SDK
│   │   ├── agents/
│   │   │   ├── memory_manager.py        # Graphiti + file fallback
│   │   │   ├── planner.py               # ← Task 5 will rewrite
│   │   │   ├── session.py
│   │   │   ├── utils.py
│   │   │   └── tools_pkg/
│   │   │       ├── registry.py          # spec-internal tool registry
│   │   │       ├── tools/
│   │   │       │   ├── memory.py        # in-agent: graphiti ops
│   │   │       │   ├── progress.py      # in-agent: status updates
│   │   │       │   ├── qa.py            # in-agent: validation
│   │   │       │   ├── subtask.py       # in-agent: subtask state
│   │   │       │   └── task_control.py  # ★ MVP MCP surface (7 tools)
│   │   │       └── http_client.py
│   │   ├── mcp_server/
│   │   │   └── tfactory_server.py       # stdio MCP entrypoint
│   │   ├── test_plan/                   # ← renamed from implementation_plan
│   │   │   ├── enums.py                 # ★ Lane enum added in Task 3
│   │   │   ├── subtask.py               # ★ .lane field added in Task 3
│   │   │   ├── phase.py
│   │   │   ├── plan.py                  # ImplementationPlan model
│   │   │   ├── story.py
│   │   │   ├── verification.py
│   │   │   └── factories.py
│   │   ├── workspaces/                  # ★ NEW in Task 3
│   │   │   └── snapshotter.py           # AIFactory → TFactory snapshot
│   │   ├── tools/
│   │   │   ├── executor.py              # in-agent tool runner (inherited)
│   │   │   ├── definitions.py
│   │   │   └── runners/                 # ★ NEW in Task 4
│   │   │       ├── docker_runner.py     # sandboxed test exec
│   │   │       ├── lane_dispatch.py     # lane → runner routing
│   │   │       └── lang_registry.py     # per-lang, per-lane tool table
│   │   ├── prompts/                     # ← Tasks 5-8 add new prompts here
│   │   ├── providers/                   # OpenAI / Anthropic / Ollama factory
│   │   ├── context/                     # project_analyzer (reused)
│   │   ├── memory/, core/, integrations/, runners/
│   ├── web-server/                      # FastAPI — Task 9 retheme
│   └── frontend-web/                    # React — Task 10 retheme
├── docker/
│   └── runners/
│       └── python.Dockerfile            # ★ NEW in Task 4
├── .claude/
│   └── skills/
│       └── handover-to-tfactory/        # ★ NEW in Task 2
│           └── SKILL.md
├── companion-skills/                    # ★ NEW in Task 2
│   └── aifactory-handover-to-tfactory/
│       └── SKILL.md                     # installs into AIFactory
├── docs/                                # Jekyll source for this site
│   ├── _config.yml
│   ├── index.md
│   ├── architecture.md                  # ← you are here
│   ├── progress.md
│   ├── design-plan.md
│   ├── spec.md
│   ├── technical-spec.md
│   ├── tests.md
│   └── tasks.md
├── .agent-os/
│   └── specs/2026-05-28-tfactory-mvp-walking-skeleton/
│       ├── spec.md, tasks.md
│       └── sub-specs/{technical-spec.md, tests.md}
├── scripts/
│   ├── verify-fork.sh                   # ★ NEW in Task 1
│   ├── start-tfactory-mcp.sh            # renamed from start-aifactory-mcp.sh
│   └── ... (other inherited scripts)
├── tests/
│   ├── test_tfactory_mcp_tools.py       # ★ Task 2 (21 cases)
│   ├── test_test_plan_lane.py           # ★ Task 3 (10 cases)
│   ├── test_snapshotter.py              # ★ Task 3 (11 cases)
│   ├── test_docker_runner.py            # ★ Task 4 (28 cases)
│   ├── test_lang_registry.py            # ★ Task 4 (10 cases)
│   ├── test_lane_dispatch.py            # ★ Task 4 (10 cases)
│   └── ... (inherited; some quarantined)
└── charts/tfactory/                     # Helm chart (renamed)
```

★ = TFactory-original work; everything else inherited from the AIFactory
fork and adapted by string-replace.

## Workspace layout (runtime)

```
~/.tfactory/
├── projects.json                        # { projects: [{ id, name, root_path, created_at }] }
└── workspaces/
    └── {project_id}/
        └── specs/
            └── {spec_id}/
                ├── task.md              # handover payload (markdown)
                ├── status.json          # lifecycle state, lane_progress
                ├── context/             # ← populated by Task 3 snapshotter
                │   ├── source.json      #   { aifactory_spec_dir, branch, base_ref, sha, ... }
                │   ├── aifactory_spec.md    (mode 0o444)
                │   ├── aifactory_plan.json  (mode 0o444)
                │   └── diff.patch       #   base_ref..branch
                ├── tests/               # ← Gen-Functional writes pytest files (Task 6)
                │   └── functional/
                ├── findings/            # ← SAST/DAST/fuzz/mutation (phases 2-5)
                ├── logs/                # ← per-agent transcripts (Tasks 5+)
                ├── memory/              # ← session insights (Tasks 5+)
                ├── report.md            # ← Triager output (Task 8)
                └── report.json
```

Cross-reference is one-way: TFactory reads `~/.aifactory/workspaces/{project_id}/specs/{spec_id}/`
**read-only** at handover time, copies relevant files into `context/`
at mode 0o444, and operates on the snapshot thereafter. The upstream
AIFactory spec can change without breaking in-flight TFactory work.

## Handover dataflow

```
┌────────────────────┐        /handover-to-tfactory
│ AIFactory project  │  ───►  (Claude Code skill)
│ at <root_path>     │              │
│ branch = feature/* │              │  MCP call over stdio
└────────────────────┘              ▼
                            mcp__tfactory__task_create_and_run
                            { project_id, spec_id, branch, base_ref, confirm }
                                    │
                                    ▼
         ┌──────────────────────────────────────────────┐
         │ apps/backend/agents/tools_pkg/tools/         │
         │   task_control.py                            │
         │ ─────────────────────────────────────────── │
         │ • look up project in projects.json           │
         │ • mkdir ~/.tfactory/workspaces/.../specs/X/  │
         │ • write task.md + status.json (status=pending│
         │ • call snapshot_aifactory_spec(...)          │
         │   if SnapshotError → rollback + MCP error    │
         │ • return { task_id, spec_dir, portal_url }   │
         └────────────────┬─────────────────────────────┘
                          │
                          ▼
            ~/.tfactory/workspaces/.../specs/X/
                          │
                          │  Tasks 5-8 will pick up here:
                          ▼
                  Planner agent reads context/aifactory_spec.md +
                  context/diff.patch and emits test_plan.json
                          │
                          ▼
                  Gen-Functional reads test_plan.json, generates
                  pytest files into tests/functional/ in the workspace
                          │
                          ▼
                  Executor calls dispatch_lane("functional", ...)
                  → DockerRunner.run_pytest(...) in tfactory-runner-python
                          │
                          ▼
                  Evaluator scores coverage delta + flake-lint + mutate-
                  and-check sanity probe → per-test verdicts
                          │
                          ▼
                  Triager dedups + ranks + renders report.md, commits
                  accepted tests to the AIFactory feature branch, runs
                  `gh pr comment <pr>` with the report body
```

## Runner stack (Task 4)

```
                ┌──────────────────────┐
                │   dispatch_lane()    │   thin lane → runner router
                │   lane_dispatch.py   │
                └──────────┬───────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        │                  │                      │
   functional        sast/deps/secrets       dast/fuzz/mutation
        │                  │                      │
        ▼                  ▼                      ▼
  DockerRunner       (phase 3:                (phase 2/5:
  docker_runner.py    native walker            LaneNotImpl
  ─────────────       — not lit)              with phase tag)
  build_argv()  ←── pure function: argv list
                    --network=none
                    --read-only
                    --cpus=2 --memory=2g
                    --pids-limit=512
                    -v repo:/work:ro
                    -v scratch:/scratch:rw
                    --tmpfs /tmp:rw,size=64m
  run()
   └── subprocess.run(...)
   └── collect /scratch/junit.xml + /scratch/coverage.xml
   └── DockerRunResult { returncode, stdout, stderr, argv,
                          junit_xml_path, coverage_xml_path }
```

`DockerRunner` wraps `subprocess` rather than the docker SDK so swapping
docker ↔ podman is a config change. Binary picked from
`TFACTORY_CONTAINER_BIN` env or constructor; default `docker`.

## Tool registry (Task 4)

| Language | Functional | SAST | Deps | Secrets | DAST | Fuzz | Mutation |
|---|---|---|---|---|---|---|---|
| **Python** | pytest ★ | semgrep | pip-audit | gitleaks | zap-cli | atheris | mutmut |
| **TypeScript** | vitest | semgrep+eslint | npm-audit | gitleaks | zap-cli | jsfuzz | stryker |
| Go | — | — | — | — | — | — | — |
| Rust | — | — | — | — | — | — | — |
| Ruby | — | — | — | — | — | — | — |

★ = the only `available_at_mvp=True` cell.
[`lang_registry.py`](https://github.com/olafkfreund/TFactory/blob/main/apps/backend/tools/runners/lang_registry.py)
holds the live source.

## MCP surface (Task 2)

Seven tools exposed over stdio to Claude Code via
[`.mcp.json`](https://github.com/olafkfreund/TFactory/blob/main/.mcp.json) and
[`scripts/start-tfactory-mcp.sh`](https://github.com/olafkfreund/TFactory/blob/main/scripts/start-tfactory-mcp.sh):

| Tool | Purpose | Side effects |
|---|---|---|
| `task_create_and_run` | Create a TFactory workspace for an AIFactory spec | mkdir workspace + snapshot AIFactory spec |
| `task_status` | Read lifecycle state | read-only |
| `task_list` | List tasks; filter by project / status | read-only |
| `project_list` | List registered AIFactory projects | read-only |
| `project_create` | Register an AIFactory project | append to projects.json |
| `report_get` | Fetch report md / json | read-only |
| `task_rerun` | Re-execute one lane (functional only at MVP) | bump rerun_count + reset lane state |

All seven defined in
[`task_control.py`](https://github.com/olafkfreund/TFactory/blob/main/apps/backend/agents/tools_pkg/tools/task_control.py).

## Module dependency graph

```
        test_plan/  ◄────────┐
        (Lane, Subtask)      │
              ▲              │
              │              │ imports Lane (Task 3+)
              │              │
       workspaces/           │
       snapshotter           │
              ▲              │
              │ used by      │
              │              │
   tools_pkg/tools/          │
     task_control.py  ─────► │
              ▲              │
              │ registered   │
              │              │
       mcp_server/
       tfactory_server.py

       tools/runners/   ◄───── (Task 6 Gen-Functional will import)
       ├── docker_runner.py
       ├── lang_registry.py
       └── lane_dispatch.py
                  ▲
                  │ used by
                  │
       (Task 8 Executor)
```

## What's NOT in the architecture yet

- **Planner / Generator / Evaluator / Triager agents** (Tasks 5-8). Prompts under `apps/backend/prompts/` will be authored as those tasks land.
- **Portal retheme** (Tasks 9-10). The inherited FastAPI app + React frontend are present but still configured for AIFactory's coder pipeline.
- **REST endpoints** for the seven MCP tools. Phase-9 portal will mirror them so the React frontend can read the same state.
- **CI workflow** updates. `.github/workflows/` inherited; pending Task 12's docs+tag pass.
- **factory-core shared lib**. Hard-fork trade-off — accepted infra drift for clean separation. May extract later.

## Development environment (Nix / devenv)

TFactory ships a `flake.nix` declaring a `devShells.default` that gives
you a reproducible NixOS-friendly dev shell. Entry is a single command:

```bash
nix develop
```

Or with [`direnv`](https://direnv.net/) auto-loading:

```bash
direnv allow
```

What's in the shell:

- **Python 3.13** + `uv` for venv management
- **Node.js 22** for the frontend + portal
- **`docker-client`** (the daemon lives on the host) for `DockerRunner` (Task 4)
- **`git`, `gh`, `just`, `ripgrep`, `jq`** — the tools `verify-fork.sh` and friends expect
- Shell functions: **`bootstrap-venv`** (full backend install), **`tfactory-minimal-venv`** (just pytest), **`tfactory-test`** (run the non-SDK suite), **`verify-fork`**

Env defaults (overridable per-shell):

- `TFACTORY_WORKSPACE_ROOT=~/.tfactory`
- `TFACTORY_PORTAL_PORT=3102`
- `TFACTORY_AUTO_PLAN=0` (off by default for deterministic tests; production sets to `1`)

`nix flake check` validates the shell builds across `x86_64-linux`.
`nix fmt` formats `.nix` files via `nixpkgs-fmt`. The legacy `shell.nix`
remains for `nix-shell` users; new development should prefer the flake
path.

## Cross-references

- Full design rationale + 10 locked decisions → [Design Plan]({{ '/design-plan/' | relative_url }})
- Component-level implementation detail → [Technical Spec]({{ '/technical-spec/' | relative_url }})
- TDD plan + test pyramid → [Test Coverage Spec]({{ '/tests/' | relative_url }})
- Per-task breakdown + dependency graph → [Task Breakdown]({{ '/tasks/' | relative_url }})
- Live build status → [Progress]({{ '/progress/' | relative_url }})

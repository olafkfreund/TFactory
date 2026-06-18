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

## Pipeline spine (5 lanes)

```
AIFactory finished branch  ─►  /handover-to-tfactory  ─►  TFactory MCP
                                                              │
                                                              ▼
                                                         Planner
                                                    (test_plan.json)
                                                              │
                        ┌──────────┬──────────┬──────────────┼──────────────┐
                        ▼          ▼          ▼               ▼              ▼
                    Gen-Unit   Gen-Browser Gen-API      Gen-Integration  Gen-Mutation
                        └──────────┴─────┬──┴──────────────── ┘──────────────┘
                                         ▼
                              Executor (Docker per subtask,
                             .tfactory.yml target addressing,
                              AppRuntime for browser/api)
                                         ▼
                              Evaluator  (5-signal verdicts:
                               coverage · stability · mutation ·
                               lint-promotion · semantic-relevance)
                                         ▼
                              Triager (update-in-place vs create-new
                               via .tfactory/tests-catalog.json)
                                         ▼
                              git commit + PR comment (dry-run default)
```

All five lanes are wired (TFactory v0.9.x). Lane
dispatch is gated per the `Lane` enum: `Lane.UNIT` runs pytest;
`Lane.BROWSER` runs Playwright in a per-task Nix toolchain inside an
ephemeral Kubernetes Job (RFC-0005 Tier A), capturing screenshots
(`findings/screenshots/`) and Playwright video recordings
(`findings/videos/`); `Lane.API` and `Lane.INTEGRATION` use the same
per-framework Docker runner image dispatch plus the HTTP HAR recorder from
`agents/evidence/http_recorder.py`; `Lane.MUTATION` shells out to
Stryker for TypeScript or `mutate_probe.py` for Python. Evidence
artefacts (screenshots / video / trace / HAR) are captured per test,
served by the portal and rendered in the task-detail Acceptance and
Evidence tabs, and also surfaced in the CFactory cockpit on the finished
task.

## Lane status

| Lane | Phase | Framework examples | Status |
|------|-------|--------------------|--------|
| **Unit**        | **1** | pytest, Jest, vitest | Active |
| **Browser**     | **2** | Playwright (chromium/firefox/webkit) | Active (Nix toolchain in ephemeral Kubernetes Job) |
| **API**         | **3** | pytest-httpx, supertest, dredd | Active |
| **Integration** | **4** | testcontainers-python, testcontainers-node | Active |
| **Mutation**    | **5** | mutmut, cosmic-ray, Stryker | Active |

The framework descriptor registry (`framework_registry/`) catalogs
80 frameworks across the five lanes; `.tfactory.yml` configures targets
(HTTP / kubernetes / docker_compose / feature_flag) for each lane.
The tests-catalog (`tests_catalog/`) persists cross-run continuity via
`tests-catalog.json` committed alongside generated tests.

## Review lane (analysis, opt-in)

The **review lane** (`apps/backend/agents/review_lane.py`) is an *analysis* lane
that runs an LLM "staff engineer" reviewer over the build's changed code and
writes findings to `findings/review.json`. It is **not** part of the 5-lane
test-runner spine above — it produces a complementary verify signal alongside the
test lanes and never touches the Evaluator / Triager / verdict contract.

- **Opt-in, default OFF.** `schedule_review(...)` runs the lane only when
  `TFACTORY_REVIEW_LANE=1`. When enabled it is scheduled (fire-and-forget) from
  the generator's success path and runs in parallel with the Evaluator.
- **Additive output.** It writes `findings/review.json` (a list of `findings`)
  and patches `status.json` (`status="reviewing"` → `reviewed`, with
  `review_findings_count`). It does not block or feed the verdict path.
- **Reuses proven plumbing.** The same SDK + session seam as `gen_functional`
  (`_resolve_client` / `run_agent_session`); the persona prompt is
  `prompts/review_lane.md`, adapted from the vendored `code-reviewer` agent.

The review lane is vendored from agent-skills and gated so the default verify path
is unchanged when the flag is unset.

## Liveness watchdog

`apps/backend/agents/liveness.py` turns a silently hung stage into an explicit
`stalled` status the portal (or a watcher) can surface. A stage that hangs leaves
`status.json` in an active "-ing" state with a frozen `updated_at`; the watchdog
compares that timestamp against `now` and, past the idle budget, flips the task to
`status="stalled"` (preserving the prior state as `stalled_from`, recording
`stall_idle_seconds`, and emitting a stage event).

- Only the four *active* statuses can stall — `planning`, `generating`,
  `evaluating`, `triaging` (`ACTIVE_STATUSES`). Handoff and terminal/failed states
  are excluded so a settled task is never clobbered.
- `evaluate_liveness` is pure compute (fully unit-testable); `mark_stalled` is the
  best-effort writer; `check_and_mark` is the convenience the periodic driver calls.
- Idle budget defaults to 900s (15 min), overridable via
  `TFACTORY_STALL_DEADLINE_SECONDS`. Missing/corrupt `status.json` or an
  unparseable `updated_at` fails safe (never flips).

## CLI commands

```bash
# Scaffold a new .tfactory.yml + empty tests-catalog.json
python -m cli init
python -m cli init --non-interactive --target-name api \
    --target-type http --base-url https://api.staging.example.com

# Migrate v0.1 workspace tests to the new catalog format
python -m cli migrate v0_1_catalog
python -m cli migrate v0_1_catalog --dry-run
```

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
│   │   │       │   └── task_control.py  # MVP MCP surface (7 tools)
│   │   │       └── http_client.py
│   │   ├── mcp_server/
│   │   │   └── tfactory_server.py       # stdio MCP entrypoint
│   │   ├── test_plan/                   # ← renamed from implementation_plan
│   │   │   ├── enums.py                 # Lane enum added in Task 3
│   │   │   ├── subtask.py               # .lane field added in Task 3
│   │   │   ├── phase.py
│   │   │   ├── plan.py                  # ImplementationPlan model
│   │   │   ├── story.py
│   │   │   ├── verification.py
│   │   │   └── factories.py
│   │   ├── workspaces/                  # NEW in Task 3
│   │   │   └── snapshotter.py           # AIFactory → TFactory snapshot
│   │   ├── tools/
│   │   │   ├── executor.py              # in-agent tool runner (inherited)
│   │   │   ├── definitions.py
│   │   │   └── runners/                 # NEW in Task 4
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
│       └── python.Dockerfile            # NEW in Task 4
├── .claude/
│   └── skills/
│       └── handover-to-tfactory/        # NEW in Task 2
│           └── SKILL.md
├── companion-skills/                    # NEW in Task 2
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
│   ├── verify-fork.sh                   # NEW in Task 1
│   ├── start-tfactory-mcp.sh            # renamed from start-aifactory-mcp.sh
│   └── ... (other inherited scripts)
├── tests/
│   ├── test_tfactory_mcp_tools.py       # Task 2 (21 cases)
│   ├── test_test_plan_lane.py           # Task 3 (10 cases)
│   ├── test_snapshotter.py              # Task 3 (11 cases)
│   ├── test_docker_runner.py            # Task 4 (28 cases)
│   ├── test_lang_registry.py            # Task 4 (10 cases)
│   ├── test_lane_dispatch.py            # Task 4 (10 cases)
│   └── ... (inherited; some quarantined)
└── charts/tfactory/                     # Helm chart (renamed)
```

Entries marked "NEW" / "Task N" are TFactory-original work; everything else
is inherited from the AIFactory fork and adapted by string-replace.

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
                ├── findings/            # ← verdicts.json + triage_report + evidence/
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
                          │  the pipeline picks up here:
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
   unit / browser        api / integration         mutation
        │                  │                      │
        ▼                  ▼                      ▼
  DockerRunner       DockerRunner             mutate probe
  (pytest / jest /    + AppRuntime             (mutmut / Stryker
   playwright)        (browser lane)            per language)
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

Lane spine — security scanning is out of scope (delegated to dedicated
pipelines); see `apps/backend/tools/runners/lang_registry.py` for the source
of truth.

| Language | Unit | Browser | API | Integration | Mutation |
|---|---|---|---|---|---|
| **Python** | pytest | playwright-python | httpx+pytest | testcontainers | mutmut |
| **TypeScript** | jest | playwright | supertest | testcontainers-node | stryker |
| Java / .NET | — | — | — | — | — (v0.3+) |
| Go / Rust / Ruby | — | — | — | — | — (v0.4+) |

All five lanes are lit for Python and TypeScript.
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

## Subtask lane + timing on the task API

`GET /api/tasks/{id}` exposes per-subtask **lane** and **timing** so CFactory's
test-stage execution diagram can render a live lane pipeline. Each subtask in the
response carries three additive, optional fields alongside the existing
`id` / `title` / `status` / `files` / `verification`:

| Field | Meaning |
|---|---|
| `lane` | the test lane the subtask belongs to — `unit` / `browser` / `api` / `integration` / `mutation` (the [`Lane`](https://github.com/olafkfreund/TFactory/blob/main/apps/backend/test_plan/enums.py) enum spine) |
| `started_at` | ISO-8601 timestamp the subtask began (or `null`) |
| `completed_at` | ISO-8601 timestamp the subtask finished (or `null`) |

All three are populated only on lane-tagged test plans and tolerate absence
(`null`) on older or untagged plans — no behaviour change for existing consumers.
The subtask `status` is a free-form string (rather than a fixed enum) so lane
states such as `stuck` / `blocked` round-trip cleanly to the cockpit. Both the
construction site and the serializer live in
[`apps/web-server/server/routes/tasks.py`](https://github.com/olafkfreund/TFactory/blob/main/apps/web-server/server/routes/tasks.py)
(the `Subtask` model).

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

## Credential Broker (epic #62)

A pluggable secrets layer (`apps/backend/tfactory_secrets/`) so agents can
authenticate to cloud environments without secrets in the repo. It mirrors the
LLM-provider patterns and extends — rather than replaces — the existing
`core/mcp_credentials.py` ambient chain.

```
   agent / MCP tool ─► CredentialBroker.resolve_cloud("gcp"|"aws"|"azure"|"k8s")
                       │  (1) egress gate: .tfactory.yml egress.enabled?  (default OFF)
                       │  (2) backend-fetch head, else ambient mcp_credentials chain
                       ▼
            get_secrets_backend(name)  ◄─ infer_backend_from_ref()  (refs.py)
                       │   env · localfile(sops/age/agenix) · vault ·
                       │   azure_keyvault · aws_secrets_manager · gcp_secret_manager
                       ▼
            materialise ephemerally → env vars + 0600 cred files (kubeconfig,
            ADC json) in a per-task scratch dir, wiped on close()/atexit
                       ▼
            inject into core/client.py agent env (no-op unless egress enabled)
```

| Module | Role |
|---|---|
| `__init__.py` | `SecretsBackend` ABC + `SecretRef` / `SecretValue` (value-redacting `repr`) |
| `refs.py` | per-scheme ref parsing + backend routing (mirrors `infer_provider_from_model`) |
| `factory.py` | `get_secrets_backend()` registry + alias map + lazy SDK import |
| `backends/` | `env`, `localfile`, `vault`, `azure_keyvault`, `aws_secrets_manager`, `gcp_secret_manager` |
| `broker.py` | `CredentialBroker` — cloud resolution, ephemeral materialise + wipe, `inject_task_credentials` |
| `egress.py` | `.tfactory.yml` egress gate + secret-free manifest + badge |
| `redaction.py` | value-based + pattern redaction; `RedactingFilter` for loggers |
| `cli.py` | `python -m tfactory_secrets.cli audit\|doctor\|resolve` |

Design: `docs/plans/2026-05-30-credential-broker-design.md`. Reference:
`guides/credentials.md`. Cloud SDKs are optional (lazy-imported); a missing
package degrades only that backend to `available() == False`.

## Network-lane SSRF guard + fail-closed auth bind (#361 / #362)

The browser / api / integration lanes fetch a **target URL that arrives in the
AIFactory handoff** — i.e. attacker-influenceable input — so the runner stack guards
it before any fetch:

```
   handoff target URL ─► net_guard.assert_safe_target_url(url, allow_*…)
                         │  resolve host (literal IP or every DNS answer)
                         │  ALWAYS block: 169.254.0.0/16 · fe80::/10 · fc00::/7
                         │               (cloud-metadata / link-local — no override)
                         │  block loopback unless allow_loopback=True
                         │               (AppRuntime compose health-poll opts in)
                         │  block RFC-1918 unless allow_private=True
                         ▼
              UnsafeTargetURLError  ─► lane refuses to fetch
```

| Module | Role |
|---|---|
| `apps/backend/tools/runners/net_guard.py` | stdlib-only SSRF guard; `assert_safe_target_url` / `is_safe_target_url` |
| `apps/backend/tools/runners/app_runtime.py` | compose health-poll calls the guard with `allow_loopback=True` (localhost is legitimate here) |
| `apps/backend/tools/runners/lane_dispatch.py` | validates the untrusted handoff URL with no allow-flags before dispatch |
| `tests/test_net_guard.py` | range coverage incl. mixed public/internal DNS answers |

The metadata / link-local ranges are blocked **unconditionally** — there is no global
"off" switch, so a hostile input cannot flip one. The local compose app is reachable
only via an explicit `allow_loopback` opt-in at the trusted call site.

The web-server enforces a parallel rule at boot
(`apps/web-server/server/config.py`): it **refuses to start** when `DISABLE_AUTH=true`
while `HOST` is not loopback (an unauthenticated control plane on the network), unless
an explicit escape-hatch env var is set. A live pytest run is exempted via
`PYTEST_CURRENT_TEST` (set only while pytest runs, never in a deployment) so the
trusted-sandbox CI bind on `0.0.0.0` still works while production stays protected. The
issue-dispatch workflow (`.github/workflows/tfactory-dispatch.yml`) likewise routes the
untrusted issue title through an `env:` var rather than interpolating it into a `curl`
command line (Actions script-injection fix).

## What's NOT in the architecture yet

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
- `TFACTORY_PORTAL_PORT=3103`
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

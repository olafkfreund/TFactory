---
layout: default
title: Progress
permalink: /progress/
nav_order: 8
---

# Progress

Live build status of the TFactory MVP. Each task is one GitHub issue + one
commit on `main`. Numbers update by hand as commits land — last refresh on
2026-05-28.

## At-a-glance

```
Phase 1 (MVP — walking skeleton)
  ████████░░░░░░░░░░░░░░░░░░  4 of 12 tasks delivered

  Done:    #2  #3  #4  #5
  Ready:   #6 (Planner)  #7 (Gen-Functional)        — parallel-able
  Blocked: #8  #9  #10  #11  #12  #13
  Carry-forward: #14 (venv install + smokes)
```

## Closed tasks

| # | Task | Commit | Lines | Closed |
|---|---|---|---|---|
| [#2](https://github.com/olafkfreund/TFactory/issues/2) | Task 1: Scaffold TFactory by hard-forking AIFactory | [`d3e321e`](https://github.com/olafkfreund/TFactory/commit/d3e321e) | +317,433 / 1,306 files | 2026-05-28 |
| [#3](https://github.com/olafkfreund/TFactory/issues/3) | Task 2: MCP server + `/handover-to-tfactory` skill | [`6b72011`](https://github.com/olafkfreund/TFactory/commit/6b72011) | +1,100 / −597 | 2026-05-28 |
| [#4](https://github.com/olafkfreund/TFactory/issues/4) | Task 3: Workspace + state model + snapshotter | [`a131c2c`](https://github.com/olafkfreund/TFactory/commit/a131c2c) | +750 / −16 | 2026-05-28 |
| [#5](https://github.com/olafkfreund/TFactory/issues/5) | Task 4: Docker runner + lane dispatcher | [`d74bb46`](https://github.com/olafkfreund/TFactory/commit/d74bb46) | +1,133 | 2026-05-28 |

Closing rationale: each task is functionally delivered, dependents built
on top successfully, and the only remaining sub-tasks wait on
user-driven `npm run install:backend` / `docker build` / manual smokes —
all consolidated in [**#14**](https://github.com/olafkfreund/TFactory/issues/14).

## What's working today

Across the four closed tasks, **~1,933 lines** of TFactory-original code
landed (excludes the inherited fork):

| Module | LoC | What it does |
|---|---:|---|
| `apps/backend/agents/tools_pkg/tools/task_control.py` | 572 | The 7 MVP MCP tools (filesystem-backed) |
| `apps/backend/workspaces/snapshotter.py` | 264 | AIFactory → TFactory read-only snapshot |
| `apps/backend/tools/runners/docker_runner.py` | 268 | Sandboxed pytest exec via docker / podman |
| `apps/backend/tools/runners/lane_dispatch.py` | 128 | Lane → runner routing + phase-tagged gates |
| `apps/backend/tools/runners/lang_registry.py` | 108 | Per-language, per-lane tool table |
| `docker/runners/python.Dockerfile` | 79 | Locked-down image — pytest + non-root + tini |
| `scripts/verify-fork.sh` | 184 | Idempotent post-fork sanity check (exit-0 clean) |
| `.claude/skills/handover-to-tfactory/SKILL.md` | 137 | TFactory-side slash command |
| `companion-skills/aifactory-handover-to-tfactory/SKILL.md` | 130 | Drop-in mirror for AIFactory's repo |

Plus the test suite covers **~90 unit cases** across:

- `test_tfactory_mcp_tools.py` — 21 cases (every MCP tool, every error path)
- `test_test_plan_lane.py` — 10 cases (Lane enum + Subtask round-trip)
- `test_snapshotter.py` — 11 cases (mode 0o444, soft fails, real-git diff)
- `test_docker_runner.py` — 28 cases (argv shape, lockdown flags, timeouts)
- `test_lang_registry.py` — 10 cases (lookups, MVP filter, unknown lang)
- `test_lane_dispatch.py` — 10 cases (lit lanes, phase-tagged errors)

All test files pass `python -m py_compile` and use the
[`conftest.py`](https://github.com/olafkfreund/TFactory/blob/main/tests/conftest.py)
pre-mock of `claude_agent_sdk` so they collect cleanly without the SDK.
Execution waits on the venv install (**#14**).

## What's next

### Ready to start (parallel)

- [**#6 — Task 5: Planner**](https://github.com/olafkfreund/TFactory/issues/6).
  Reads `context/aifactory_spec.md` + `context/diff.patch` from the
  snapshot, emits lane-tagged subtasks into `test_plan.json`. Net new
  prompt + agent module.
- [**#7 — Task 6: Gen-Functional**](https://github.com/olafkfreund/TFactory/issues/7).
  Consumes the planner's `Lane.FUNCTIONAL` subtasks, generates pytest
  files, runs them through `dispatch_lane → DockerRunner.run_pytest`.
  Includes the two MVP guardrails:
  - **Pre-flight static check**: every import + method call resolves
    against the diffed code's actual symbols (kills hallucinated tests).
  - **Flake-risk lint**: dict-order assertions, `time.sleep`, unfrozen
    `datetime.now()` etc. flagged or rejected.

### Sequential downstream

- **#8 — Task 7: Evaluator** (blocked by #7).
  Coverage delta + 3x stability re-run + LLM semantic relevance +
  mutate-and-check sanity probe.
- **#9 — Task 8: Triager + git writer** (blocked by #8).
  Dedup + rank + `report.md`/`report.json` + `git commit` accepted
  tests on the AIFactory feature branch + `gh pr comment`.
- **#10 — Task 9: Portal backend** (blocked by #9). FastAPI on `:3102`.
- **#11 — Task 10: Portal frontend** (blocked by #9; parallel with #10).
  React lane-status grid + report viewer.
- **#12 — Task 11: e2e smoke** (blocked by #10 + #11).
  The 9 verification scenarios from the design plan.
- **#13 — Task 12: Docs + tag v0.1.0-mvp** (blocked by #12).

### Carry-forward (#14)

Seven items waiting on `npm run install:backend` + `docker build`:

- `1.7` inherited pytest baseline
- `2.5` `/handover-to-tfactory` manual smoke from AIFactory
- `2.6` MCP tool tests
- `3.1 / 3.5` lane + snapshotter tests
- `4.5` docker build + integration smoke
- `4.6` docker_runner + lang_registry + lane_dispatch tests

## Dependency graph (current state)

```
   [DONE]
  ✓ #2 (Task 1)
        │
   ┌────┴────┬─────────┐
   │         │         │
✓ #3       ✓ #4      ✓ #5            (parallel batch — all closed)
(MCP+        (snap-    (docker
 skill)       shotter)   runner)
   │         │         │
   └─────────┴────┬────┘
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
    #6 (T5 Planner)      #7 (T6 Gen-Functional)        ← READY
       └─────────┬───────────┘
                 ▼
            #8 (T7 Evaluator)                          ← blocked by #7
                 │
                 ▼
            #9 (T8 Triager)                            ← blocked by #8
                 │
         ┌───────┴───────┐
         ▼               ▼
       #10 (T9        #11 (T10
       portal BE)     portal FE)                       ← blocked by #9
         └───────┬───────┘
                 ▼
            #12 (T11 e2e)                              ← blocked by #10+#11
                 │
                 ▼
            #13 (T12 docs + tag v0.1.0-mvp)            ← blocked by #12
```

## Commit timeline

```
d74bb46  Task 4: docker runner + lane dispatcher + lang registry  ★ #5
a131c2c  Task 3: workspace state model + snapshotter              ★ #4
6b72011  Task 2: MCP server + handover skill                      ★ #3
d3e321e  Task 1: initial fork from AIFactory @ 676d1d0            ★ #2
6b04856  docs: fix YAML frontmatter in spec.md
b8b0f6f  Initial commit: design + spec + Pages site
```

## Health signals

- ✅ `scripts/verify-fork.sh --no-import` — PASS (15/15 structural checks)
- ⚪ pytest — not yet run; waiting on venv install (#14)
- ⚪ docker integration — Dockerfile written; not yet built (#14)
- ✅ Pages site builds + deploys on every push to `main`
- ✅ All MCP tool descriptions visible in the catalog with their schemas
- ⚪ End-to-end handover from AIFactory → TFactory — manual smoke (#14, 2.5)

## Where the unknowns are

Things this design hasn't yet validated:

- How chatty is the Planner's prompt with realistic AIFactory specs? Token cost per task TBD.
- Does the pre-flight static check (Task 6.1) actually catch the 39%-of-failures hallucination rate the research suggests? Need real data.
- Mutation-and-check sanity probe vs full mutmut at MVP — the cheap proxy may or may not be sufficient until Phase 2 lands.
- Docker network policy for the eventual DAST lane (Phase 5) — `--network=none` default vs an opt-in bridge for ZAP attacks.

See the [Design Plan]({{ '/design-plan/' | relative_url }}#risks-and-mitigations) risk register for the full list.

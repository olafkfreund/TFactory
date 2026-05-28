---
layout: default
title: TFactory
nav_order: 1
---

# TFactory

> **Autonomous test generation + execution platform — sister project to [AIFactory](https://github.com/olafkfreund/AIFactory).**
> Status: Planning · MVP design locked 2026-05-28

TFactory receives a finished AIFactory spec, generates feature + security tests
aligned to its acceptance criteria, executes them sandboxed, evaluates quality,
commits the tests to the feature branch, and posts a coverage + findings report
back to the PR — autonomously.

## What's in scope

| Lane | Status | Phase |
|---|---|---|
| Functional (pytest) | **Active at MVP** | 1 |
| Mutation testing (mutmut) | Planned | 2 |
| SAST + deps + secrets | Planned | 3 |
| TypeScript across all lit lanes | Planned | 4 |
| DAST + fuzzing | Planned | 5 |
| Go / Rust / Ruby | Planned | 6 |

## Architecture in one diagram

```
AIFactory finished branch  ─►  /handover-to-tfactory  ─►  TFactory MCP
                                                              │
                                                              ▼
                                                         Planner
                                                              │
                              ┌──────────┬─────────┬──────────┼──────────┐
                              ▼          ▼         ▼          ▼          ▼
                          Gen-Func   Gen-SAST  Gen-DAST   Gen-Mut    (more)
                              └──────────┴────┬────┴──────────┴──────────┘
                                              ▼
                                          Executor  (Docker per task)
                                              ▼
                                          Evaluator  (separate agent)
                                              ▼
                                          Triager   ─►  git commit + PR comment
```

Six agents, four lanes (functional active at MVP), tiered sandbox (native for
static, Docker for runtime), spec-aware handover from AIFactory.

## Documentation

- **[Design Plan]({{ '/design-plan/' | relative_url }})** — full design rationale, 10 locked decisions, alternatives considered, landscape research (Diffblue, Meta TestGen-LLM, Qodo, OSS-Fuzz-Gen, XBOW, etc.), risk register
- **[Spec]({{ '/spec/' | relative_url }})** — Agent OS spec: overview, user stories, scope, deliverable
- **[Technical Spec]({{ '/technical-spec/' | relative_url }})** — architecture detail, inputs/outputs, per-component implementation
- **[Test Coverage Spec]({{ '/tests/' | relative_url }})** — TDD plan: unit / integration / e2e pyramid, mocking strategy
- **[Task Breakdown]({{ '/tasks/' | relative_url }})** — 12 numbered tasks with dependency graph, mapped to GitHub Issues

## Project tracking

- **Epic + sub-issues**: [github.com/olafkfreund/TFactory/issues](https://github.com/olafkfreund/TFactory/issues)
- **Source**: [github.com/olafkfreund/TFactory](https://github.com/olafkfreund/TFactory)
- **Sister project (upstream)**: [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)

## Key design decisions

1. **Hard fork** of AIFactory (not shared-core) — accepted infra drift in exchange for clean separation.
2. **Spec-aware handover** — TFactory only operates on AIFactory specs at MVP; not a generic external-repo scanner.
3. **Six-agent topology** — shared Planner, per-lane Generators, shared Executor, structurally-separate Evaluator, Triager.
4. **Tiered sandbox** — native for SAST, Docker per-task for anything that runs code.
5. **Python + TypeScript** for the full vision; **Python only** at MVP.
6. **Auto-commit to AIFactory's feature branch** + PR comment with report.
7. **Walking-skeleton MVP** — functional lane only, full pipeline end-to-end; everything else is phase 2-6.

Full decisions table in the [Design Plan]({{ '/design-plan/' | relative_url }}).

## License

MIT — see [`LICENSE`](https://github.com/olafkfreund/TFactory/blob/main/LICENSE).

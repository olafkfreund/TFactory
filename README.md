# TFactory

**Autonomous test generation + execution platform — sister project to [AIFactory](https://github.com/olafkfreund/AIFactory).**

TFactory ingests a finished AIFactory spec, generates feature + security tests
aligned to its acceptance criteria, runs them in a sandbox, evaluates quality,
commits the tests to the feature branch, and posts a coverage + findings report
to the PR — autonomously.

> Status: **Planning · MVP design locked 2026-05-28**.
> Code scaffolding has not started yet — see the spec and task list to follow along.

## Docs

Full project documentation is published as a GitHub Pages site:
**https://olafkfreund.github.io/TFactory/**

Direct links:

- [Design Plan](https://olafkfreund.github.io/TFactory/design-plan/) — full rationale, 10 locked decisions, landscape research, risk register
- [Spec](https://olafkfreund.github.io/TFactory/spec/) — Agent OS spec
- [Technical Spec](https://olafkfreund.github.io/TFactory/technical-spec/) — architecture detail
- [Test Coverage Spec](https://olafkfreund.github.io/TFactory/tests/) — TDD plan
- [Task Breakdown](https://olafkfreund.github.io/TFactory/tasks/) — 12 tasks with dependency graph

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

## Status by lane

| Lane | Status | Phase |
|---|---|---|
| Functional (pytest) | Active at MVP | 1 |
| Mutation testing (mutmut) | Planned | 2 |
| SAST + deps + secrets | Planned | 3 |
| TypeScript across all lit lanes | Planned | 4 |
| DAST + fuzzing | Planned | 5 |
| Go / Rust / Ruby language ramp | Planned | 6 |

## License

[MIT](LICENSE).

# TFactory

**Autonomous test generation + execution platform — sister project to [AIFactory](https://github.com/olafkfreund/AIFactory).**

TFactory ingests a finished AIFactory spec, generates feature + security tests
aligned to its acceptance criteria, runs them in a sandbox, evaluates quality,
commits the tests to the feature branch, and posts a coverage + findings report
to the PR — autonomously.

> Status: **MVP build in progress · 5 of 12 tasks delivered · Task 6 (Gen-Functional) is next.**
> See [Progress](https://olafkfreund.github.io/TFactory/progress/) for the live build log.

## Quickstart (NixOS / flake-based)

```bash
# One-command dev environment via the flake:
nix develop

# (inside the shell)
tfactory-minimal-venv   # creates apps/backend/.venv with just pytest+pytest-asyncio
tfactory-test           # runs the 120-case non-SDK suite (~1s)

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

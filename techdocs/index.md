# TFactory

> Autonomous test-generation + execution platform.

TFactory receives a finished feature on a branch, generates tests **aligned to the
acceptance criteria**, runs them in a Docker sandbox, scores them with a
**5-signal verdict pipeline**, and emits a ranked **triage report** ready to commit
and post to the PR.

It is built as the test stage of the DataSeek **Factory platform** (the PARR
spine: AIFactory → TFactory → PFactory → CFactory), with the AIFactory handover as
its warm-start wedge — but it runs standalone against any acceptance-criteria
source.

- **Status:** v0.5.0
- **Repository:** <https://github.com/olafkfreund/TFactory>
- **License:** MIT OR GPL-3.0
- **Maintainer:** DataSeek Team

## Why it exists

Most AI test tools generate from *code*, not *intent*, and prove quality with a
coverage number. The result is low-value assertion tests and flaky tests that
erode trust. TFactory generates to the **acceptance criteria**, then gates every
test on five independent signals before it trusts it:

| Signal | What it proves |
|--------|----------------|
| **Coverage delta** | The test actually exercises new lines (Cobertura set-math). |
| **3× stability** | The test is not flaky across repeated runs. |
| **Mutate-and-check** | The test *fails* when an assertion is mutated (it asserts behaviour, not just executes it). |
| **Flake-lint promotion** | Static flake-risk patterns are promoted to verdicts. |
| **LLM semantic relevance** | The test is meaningfully tied to the AC, judged by an LLM. |

## The platform at a glance

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
   (#6)        (#7)             (no LLM)    (#8)         (#9)
```

| Component | What it is | Docs |
|-----------|------------|------|
| **tfactory-backend** | The 4-agent pipeline + CLI + providers + memory | [Pipeline](architecture/pipeline.md) |
| **tfactory-web-server** | FastAPI REST + WebSocket + MCP proxies (~300 routes) | [Web REST API](apis/rest-api.md) |
| **tfactory-frontend-web** | React 19 + Vite browser portal | [Frontend](frontend.md) |
| **tfactory-mcp-server** | Stdio JSON-RPC MCP control plane | [MCP server](apis/mcp-server.md) |

## Where to start

- **Understand the design:** [Architecture overview](architecture/overview.md)
- **Drive it from an agent:** [MCP server](apis/mcp-server.md)
- **Integrate over HTTP:** [Web REST API](apis/rest-api.md)
- **Run / configure it:** [Configuration](configuration.md)
- **The "why" behind choices:** [Architecture decisions](decisions.md)

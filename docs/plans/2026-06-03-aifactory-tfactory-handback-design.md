# Bidirectional AIFactory ↔ TFactory Integration — Design

> Created: 2026-06-03
> Status: Design (approved, pre-implementation)
> Repos: TFactory (`/mnt/data/Source-home/GitHub/TFactory`) +
> AIFactory (`/home/olafkfreund/Source/GitHub/AIFactory`)
> Related: handover skills, Triager completion callback (#85),
> `agents/cloud/issues.py`, `agents/visual_inspection/correction_plan.py`

## Summary

Close the loop between AIFactory (spec-driven *plan → code → ship*) and
TFactory (autonomous *test → verdict → triage*).

- **Direction A — test (already exists):** AIFactory hands a finished feature
  to TFactory via the `/handover-to-tfactory` skill + the
  `mcp__tfactory__task_create_and_run` tool. TFactory snapshots the spec, runs
  Planner → Gen-Functional → Executor → Evaluator → Triager, and emits
  `findings/triage_report.{md,json}` + per-test verdicts.
- **Direction B — correct (NEW):** when TFactory's verdicts contain failures
  (`reject` / failing lanes / visual-inspection fail), TFactory packages a
  **correction request** and hands it **back** to AIFactory, where the existing
  **QA Fixer** agent applies the fixes on the *same* spec.
- **Direction C — loop (phased, "ideally"):** after AIFactory corrects,
  TFactory re-tests automatically, bounded by a correction-cycle cap that flips
  to `stuck` for a human — mirroring the existing `replan_count >= 2 → stuck`
  rule.

### Design keystone

TFactory **never touches AIFactory's filesystem**. It records only the
AIFactory `{project_id, spec_id, api_url}` and POSTs a fix-request markdown;
**AIFactory's own receiver** resolves `<project>/.aifactory/specs/<spec_id>/`
and runs the QA Fixer (`qa/fixer.py::run_qa_fixer_session`), which is literally
built to read `QA_FIX_REQUEST.md`. This separation dissolves the path-model
mismatch (AIFactory `<project>/.aifactory/specs/` vs TFactory's snapshot under
`~/.tfactory/workspaces/`): each side owns its own spec storage; the wire
payload is plain markdown + a small JSON envelope.

### Prior art

This is the closed-loop *verifier* pattern from agentic SWE (SWE-agent /
OpenHands test→patch→re-test) and CI auto-fix bots (Renovate/Dependabot):
the universal safety rails are **bounded iteration** and a **human stop-gate**.
Both are honored here (cycle cap → `stuck`; dry-run-first + opt-in send).

## Resolved decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Hand-back transport | **New AIFactory `task_apply_correction` → `QA_FIX_REQUEST.md` on the original spec + QA Fixer** | Keeps one spec / one `task_id` (history + tight loop); reuses the agent built for exactly this. Spawning a new spec via `task_create_and_run` would re-plan+re-code from scratch and lose the link. |
| 2 | Trigger | **Dry-run-first + opt-in auto-send** | TFactory always *prepares* the artifact; *sending* is gated behind an explicit env opt-in + confirm. Mirrors `git_writer` / `pr_comment` / cloud-issues and the no-automatic-pushes policy. |
| 3 | Loop scope | **Phase it** — open loop first (P1–P5), closed loop later (P6) | Ships the 80% value safely; designs the full loop but defers cross-app polling. |
| 4 | Correction payload | Reuse `triage_report` + visual `correction_plan`, map to `QA_FIX_REQUEST.md` | No new analysis; deterministic transform of artifacts that already exist. |
| 5 | Traceability / path model | Persist `aifactory: {project_id, spec_id, api_url, task_id}` in TFactory's `source.json`; AIFactory resolves its own spec dir | TFactory targets the original spec without knowing AIFactory's layout. |
| 6 | Code ownership | TFactory owns `agents/handback/` (build/render/send) + the skill; AIFactory owns the receiver (REST route + MCP tool + qa_fixer trigger) | Each repo owns its half of the wire. |

## Architecture

```
  ┌─────────────┐   A. test (exists)      ┌──────────────┐
  │  AIFactory  │ ─────────────────────►  │   TFactory   │
  │  spec NNN   │  handover skill +       │ Planner→…→   │
  │  qa/fixer   │  task_create_and_run    │   Triager    │
  └─────────────┘                         └──────┬───────┘
        ▲                                         │ verdicts: reject / fail
        │  B. correct (NEW)                       ▼
        │  POST /api/tasks/{id}/apply-correction  agents/handback/
        │  (MCP task_apply_correction)      ◄──── build → render → send
        │  → writes QA_FIX_REQUEST.md             (dry-run-first, opt-in)
        │  → runs qa_fixer (qa_fixer-only)        writes findings/handback_request.{md,json}
        └─────────────────────────────────────────┘
            C. loop (P6): poll task_status → task_rerun, correction_cycle >= N → stuck
```

## Components

### TFactory — `agents/handback/` (mirrors `agents/cloud/issues.py` + `agents/visual_inspection/correction_plan.py`)

**`request.py`** — pure, deterministic, no LLM, no network.
```
build_correction_request(
    verdicts: dict,            # findings/verdicts.json
    triage_report: dict,       # findings/triage_report.json
    source: dict,              # context/source.json (has aifactory ref)
    *, visual_correction_plan: str | None = None,
) -> CorrectionRequest
```
- Selects failing items: verdict `reject`, plus failing lanes / visual-inspection
  fails when present.
- Per failure: the failing test id + file + lane, the AC it maps to (from the
  triage report), the observed failure (assertion error / verdict reason), and a
  suggested fix direction.
- The visual reuse is a **prose string**, not a structured object: the visual
  module exposes `render_correction_plan(...)` (returns markdown prose), so the
  caller passes that string as `visual_correction_plan` when the source is a
  visual inspection. There is no `correction_plan` dict.
- Carries the AIFactory target (`source["aifactory"]`).
- Returns `nothing_to_hand_back` sentinel when there are no failures.

**`render.py`** — `render_fix_request_md(request) -> str`: deterministic
markdown shaped like AIFactory's `QA_FIX_REQUEST.md` (title, summary,
per-failure sections). Snapshot-testable.

**`send.py`**
```
send_correction(
    request: CorrectionRequest,
    *, dry_run: bool = True, confirm: bool = False,
    sender_fn: Callable[[dict], dict] = DEFAULT_SENDER,
) -> SendResult
```
- **Always** writes `findings/handback_request.md` + `findings/handback_request.json`.
- POSTs to AIFactory **only** when `dry_run is False and confirm`.
- `sender_fn` is the injectable AIFactory client (default → AIFactory REST/MCP).
  Tests pass a fake; the suite never reaches a real AIFactory.
- Unreachable / 4xx/5xx → graceful `SendResult(ok=False, …)`; never raises into
  the pipeline (best-effort, like the completion webhook).

**Triager completion hook (#85).** On terminal status with failures, the
Triager *prepares* the artifact when `TFACTORY_HANDBACK_PREPARE` (default ON);
*sends* only when `TFACTORY_HANDBACK_SEND=1` (default OFF) — exact mirror of
`TFACTORY_TRIAGER_GIT_WRITE`. Targets are confirmed (the send path takes
`confirm`). A failing hand-back never changes the TFactory task's own terminal
status.

### AIFactory — the receiver (cross-repo, small)

**REST** — `POST /api/tasks/{task_id}/apply-correction`
(`apps/web-server/server/routes/execution.py`, alongside `create-and-run` /
`start`). Body: `{ fix_request_md: str, source?: str, confirm: bool }`.
Handler seam:
```
apply_correction(spec_dir, fix_request_md, *, confirm) -> dict
```
- `task_id` is `{project_id}:{spec_id}` → resolves `<project>/.aifactory/specs/<spec_id>/`.
- `confirm=false` → preview (validates the spec exists, returns `would_write`,
  writes nothing, starts nothing).
- `confirm=true` → writes `QA_FIX_REQUEST.md` into the spec dir and starts a
  **qa_fixer-only** run.
- Returns `{ success, task_id, status }`.

> **P3 wiring reality (do not assume a `mode` switch exists):**
> `run_qa_fixer_session` is today reachable only via
> `qa/loop.py::run_qa_validation_loop` (the full review→fix loop) and is **not**
> wired into `execution.py`'s start path. AIFactory's `StartTaskRequest.mode`
> only toggles prompt verbosity (`quick` vs `full`) — it does **not** select
> agents. So P3 must explicitly create the qa-fixer-only entry point: have
> `apply_correction` invoke `run_qa_fixer_session` **directly** (preferred), or
> thread a new agent-selection mode through `agent_service`/`run.py`. Treat this
> as net-new wiring, not a reuse of an existing selector.

**MCP** — `task_apply_correction(project_id, spec_id, fix_request_md, source?, confirm)`
in `apps/backend/mcp_server/aifactory_server.py` — thin wrapper over the REST
route (matches how `task_create_and_run` wraps `/api/tasks/create-and-run`).

### Traceability (P1, TFactory)

The handover persists the AIFactory linkage into TFactory's
`context/source.json` at task-creation time:
```json
{
  "branch": "...", "base_ref": "...", "repo": "...",
  "aifactory": {
    "project_id": "...", "spec_id": "...",
    "api_url": "http://localhost:8xxx", "task_id": "proj:spec"
  },
  "correction_cycle": 0
}
```
`task_create_and_run` already receives `project_id` + `spec_id`; `api_url`
comes from the handover env (`TFACTORY_AIFACTORY_API_URL`, default
`http://localhost:8000` — verify AIFactory's web-server port during P1).

## Data model

| Artifact | Location | Purpose |
|----------|----------|---------|
| `source.json` (extended) | `<spec_dir>/context/` | adds `aifactory{}` + `correction_cycle` |
| `handback_request.json` | `<spec_dir>/findings/` | envelope: aifactory_task_id, failing tests, dry_run, source, md path |
| `handback_request.md` | `<spec_dir>/findings/` | the `QA_FIX_REQUEST.md`-shaped payload |
| `QA_FIX_REQUEST.md` | AIFactory `<project>/.aifactory/specs/<spec_id>/` | written by the receiver; read by qa/fixer.py |

`handback_request.json`:
```json
{
  "task_id": "<tfactory task>",
  "aifactory_task_id": "proj:spec",
  "generated_at": "<iso-Z>",
  "dry_run": true,
  "source": "triage" | "visual_inspection",
  "failing_tests": [
    {"test_id": "...", "file": "...", "lane": "unit",
     "verdict": "reject", "reason": "...", "assertion_error": "...?",
     "acceptance_criterion": "...?"}
  ],
  "fix_request_md_path": "findings/handback_request.md"
}
```

## Error handling & edge cases

- **No failures** → `build_correction_request` returns the
  `nothing_to_hand_back` sentinel; nothing is prepared or sent (analogous to
  `triaged_empty`).
- **AIFactory unreachable** → `send_correction` returns `ok=False`; the artifact
  stays on disk; the TFactory pipeline status is unaffected.
- **AIFactory spec missing/renamed** → receiver returns 404; TFactory records
  the failure in `handback_request.json` and surfaces it to the operator.
- **Loop (P6)** — `correction_cycle >= N` (default 2) → `stuck`. Also: if the
  same tests still fail after a correction (no progress) → `stuck`. Human takes
  over.
- **Confirm/dry-run** — `send_correction` defaults `dry_run=True`; the AIFactory
  tool defaults `confirm=false` (preview). Two independent gates.
- **Security** — outward-facing send is opt-in env + confirm; `api_url` defaults
  to localhost, remote requires an explicit env. No secrets in the payload.

## Closed loop (P6, phased)

`agents/handback/loop.py` (TFactory): after a successful send, poll AIFactory's
existing `task_status` MCP/REST until the correction reaches a terminal state,
then `mcp__tfactory__task_rerun` to re-test. Increment `correction_cycle`;
`>= N` → `stuck`. Surfaced as a `/loop`-able skill `/tfactory-fixloop`
(analogous to `/tfactory-watch`). Designed now, built after P1–P5 land.

## Testing strategy

**TFactory units (no network, no AIFactory, no LLM):**
- `build_correction_request` from canned `verdicts.json` + `triage_report.json`
  → asserts failing-test selection, AC mapping, and the visual
  `correction_plan` merge; `nothing_to_hand_back` on all-accept.
- `render_fix_request_md` → deterministic snapshot.
- `send_correction`: dry-run writes both artifacts and does **not** call
  `sender_fn`; `dry_run=False`+confirm calls `sender_fn` with the right payload;
  unreachable `sender_fn` → graceful `SendResult`.
- `source.json` round-trip: handover writes `aifactory{}`; handback reads it.
- Loop bound: `correction_cycle` increments; `>= N` → `stuck`.

**AIFactory units:**
- `apply_correction`: `confirm=false` previews without writing/starting;
  `confirm=true` writes `QA_FIX_REQUEST.md` and invokes the (mocked) qa_fixer
  start.
- MCP `task_apply_correction` wraps the REST route (HTTP mocked).

**End-to-end (BOTH apps running — explicitly out of unit scope):** real
pipeline → rejects → send → AIFactory receives → qa_fixer runs → (P6) re-test.
Requires LLM creds + Docker + both servers. Documented in
`guides/aifactory-handback.md`; referenced from `guides/e2e-smoke.md`.

## Phasing (issue-decomposable epic)

| Phase | Scope | Repo | Notes |
|-------|-------|------|-------|
| **P1** | Persist `aifactory{}` + `correction_cycle` in `source.json` at handover | TFactory | Small; unblocks targeting |
| **P2** | `agents/handback/request.py` + `render.py` + tests | TFactory | Pure-compute; reuses triage_report + correction_plan |
| **P3** | Receiver: REST `apply-correction` + MCP `task_apply_correction` + qa_fixer trigger + confirm-first + tests | **AIFactory** | Cross-repo |
| **P4** | `agents/handback/send.py` + dry-run/opt-in env + Triager #85 hook + tests | TFactory | `sender_fn` seam |
| **P5** | `/handback-to-aifactory` skill + AIFactory companion | TFactory (+ companion) | Mirrors `/handover-to-tfactory` |
| **P6** | Closed loop: `loop.py` poll → `task_rerun` → bound→`stuck` + `/tfactory-fixloop` | TFactory | The phased "ideally" |
| **Docs** | `guides/aifactory-handback.md` + round-trip section in both handover skills + CLAUDE.md | TFactory | |

## Out of scope (YAGNI)

- No new analysis: the hand-back is a deterministic transform of artifacts that
  already exist.
- No change to AIFactory's planning/coding agents — only a new inbound receiver
  + the existing QA Fixer.
- No automatic pushes; no auto-send without an explicit env opt-in.
- No multi-spec / fan-out corrections; one TFactory task → one AIFactory spec.

---
status: draft
issue: 1347
author: olafkfreund
---

# Intent: a committed `.tfactory.yml` reaches the lanes on every ingest path

## Problem

A `.tfactory.yml` committed to a repository has no effect on a spec that
arrives through `POST /api/specs/ingest` (which is also how AIFactory hands
off, #517), through the `task_create_from_spec` MCP tool, or through PFactory
pickup. The api lane cannot resolve a target on those paths, so every endpoint
test dies on `os.environ["TFACTORY_TARGET_URL"]` before touching the subject,
and the triager reports that as the subject misbehaving (#1343, #1344).

Measured on `origin/dev` at `874229d8` (2026-10-03). The issue's chain holds,
with two line numbers drifted and one link it understated:

1. The resolver reads a snapshot, not the repo. `agents/evaluator_targets.py:41`
   `_browser_target_url` opens `spec_dir/context/tfactory_yml.json` and returns
   `None` when it is absent. `_resolve_target` (`:73`), which picks the
   `docker_run` and kubernetes port-forward targets, reads the same file.
   The evaluator calls it at `agents/evaluator.py:2424`.
2. The snapshot has exactly one writer: `workspaces/snapshotter.py:257`,
   inside `snapshot_aifactory_spec` (`:138`).
3. That function has exactly one caller: `agents/tools_pkg/tools/task_control.py:747`,
   inside the `task_create_and_run` MCP tool (`:692`). The issue cites the
   line without naming the tool; it is the oldest, hand-driven path and not
   the one AIFactory or the portal uses.
4. The live paths do not reach it. `/api/specs/ingest`
   (`apps/web-server/server/routes/specs.py:303`) and `task_create_from_spec`
   (`task_control.py:928`) both build the workspace with
   `create_spec_ingest_workspace` (`task_control.py:393`). It writes
   `context/aifactory_spec.md`, `context/task_contract.json` (`:460`),
   `context/source.json` (`:556`) and materialises the build branch at
   `spec_dir/.worktree` (`:492`). The string `tfactory_yml` does not occur in
   that function; the file's only mention of `.tfactory.yml` is a tool-argument
   description (`:680`). PFactory pickup
   (`integrations/pfactory/run.py:120-136`) writes its own `context/` and has
   no snapshot step either.
5. The self-serve fallback does not cover it. `evaluator.py:581` (the issue
   says `:580`) only detects a serve command when the lane is on the Nix Job
   path, and `nix_env.py:2041` admits a contract only when
   `environment.provisioning.method == "nix"`. A contract with no
   `environment` block, which is what `/api/specs/ingest` produces unless the
   caller adds one, gets neither.

So on the paths that carry real work, both routes to a target URL are closed
and the lane runs anyway.

The understated link: `tfactory_yml.json` is not only the api lane's target
source. `agents/triager.py:1504` reads its `quality_gate` block,
`agents/evaluator.py:1729` runs its `build:` steps, and
`agents/evidence/layout.py:373` reads it for evidence layout. Every one of
those is silently inert on the ingest paths today, for the same reason.

Why it stayed hidden: the parser parses, the snapshotter writes when it runs,
the resolver reads when the file exists, and `return None` is also the
legitimate answer for "this repo declares no target". A unit test of any one
piece passes. It is the shape of #1344 and of AIFactory#1638: a component
wired to a field nothing populates on the live path.

Separately, `agents/health_gate.py:127` `resolve_target_url` has no callers
anywhere under `apps/` (confirmed: definition and its own docstring only).

## Proposed outcome

When this is done, a repository with a valid `.tfactory.yml` on the build
branch gets the same treatment whichever door the spec came through:

- An api subtask ingested via `/api/specs/ingest` with `source_branch`
  pointing at such a repo runs with `TFACTORY_TARGET_URL` set to the declared
  target's `base_url`. Proven on a real ingest, not a unit test.
- The same spec's `quality_gate` and `build:` blocks are honoured, because the
  readers above see the config.
- The resolver's "no target" answer distinguishes "repo declares none" from
  "config never reached me", so the next absence is diagnosable from the
  findings, not from `ls`.
- The three ingest paths agree on where `.tfactory.yml` is read from. Today
  `task_create_and_run` reads the shared clone (`project_entry.root_path`),
  while the ingest paths have a per-spec `.worktree` on the build branch; spec
  027 added the file on the branch, so the worktree is the tree that matters.

## Affected users and systems

- `agents/evaluator_targets.py` (resolver) and/or
  `agents/tools_pkg/tools/task_control.py` `create_spec_ingest_workspace`,
  `workspaces/snapshotter.py`, `integrations/pfactory/run.py`.
- Every reader of `context/tfactory_yml.json`: api and browser target
  resolution, `docker_run`/kubernetes targets, quality-gate policy, build
  steps, evidence layout, the planner prompt (`prompts_pkg/prompts.py:1115`).
- Anyone verifying a deployed service through AIFactory handoff or the
  portal: today their `.tfactory.yml` is dead weight.
- pfactory-friends-demo#127, the run that surfaced this.

## Constraints

- A lane that cannot obtain a target must not run and report per-test
  failures. That is #1344's subject, under fix in PR #1348; this intent
  references it and must not absorb it. Fixing #1347 alone does not make
  the no-target case honest, and fixing #1344 alone does not make the feature
  work. Both are needed; neither waits on the other.
- `snapshot_aifactory_spec` cannot simply be called from the ingest paths as
  it stands: it raises `SnapshotError` unless `~/.aifactory/workspaces/<project>/specs/<spec>`
  exists on the same host, which is never true in-cluster. Only its
  `.tfactory.yml` step (`snapshotter.py:242-262`) is wanted.
- Read the build branch, not the shared clone. The `.worktree` is the tree
  under test (#96, #742); a config that exists only on the branch must count.
- An unparseable `.tfactory.yml` must stay a warning that the run surfaces,
  as the snapshotter already does, never a silent `None`.
- No new behaviour for repos without the file. "No target declared" remains
  a legitimate, non-failing state for lanes that do not need one.
- One source of truth. Whatever is decided, the api lane, the quality gate
  and the build steps must read the config the same way; a fix that gives
  the api lane a second reader while the others stay on the snapshot
  recreates this drift.

## Open questions

1. **Where the config is made visible: snapshot at ingest, or read the
   worktree at resolve time?**
   - *Snapshot at ingest* (factor the `.tfactory.yml` step out of
     `snapshot_aifactory_spec` and call it from `create_spec_ingest_workspace`
     and the PFactory pickup). Keeps the existing contract that `context/`
     is the immutable, 0o444 record of what was verified, and fixes all five
     readers in one move. Costs: three call sites, and the snapshot is taken
     once, so a later rerun against a changed branch needs the worktree
     refresh to re-snapshot too.
   - *Resolver fallback* (`_browser_target_url` and `_resolve_target` read
     `.worktree/.tfactory.yml` when the snapshot is absent). Smallest diff
     and self-healing for existing specs. Costs: the other three readers stay
     broken unless each grows the same fallback, and the evidence record no
     longer says which config the run used.
   - I lean to the snapshot. The constraint on one source of truth decides
     it: the fallback fixes the symptom in the issue title and leaves the
     quality gate and build steps on the same dead field.
2. Should the snapshot step also run when `task_rerun` refreshes the worktree,
   so a branch that gains a `.tfactory.yml` after first ingest is picked up?
   Lean yes; small, and it is the exact sequence spec 027 went through.
3. Is `health_gate.resolve_target_url` meant to be on this path (it already
   handles the env override the api tests want), or is it dead code to be
   removed in this change? Lean: leave it out of scope and file it, so this
   stays one fix.

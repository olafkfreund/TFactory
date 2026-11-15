---
layout: post
title: "What shipped in TFactory, August to November 2026"
subtitle: "Verdicts that say only what was measured, Kotlin and Java in-cluster, verification on a pinned commit, and access control that is mostly in shadow mode. Flags and defaults are stated as the release notes state them."
date: 2026-11-15 09:00:00 +0000
author: DataSeek Team
---

TFactory has had [24 releases](https://github.com/olafkfreund/TFactory/releases)
since the [last post](/blog/2026/08/10/a-verdict-you-can-take-to-an-auditor/):
[v0.9.26](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.26)
(2026-09-18) to [v0.9.49](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.49)
(2026-10-31). Nothing was tagged from v0.9.25 (2026-07-22) to v0.9.26, so
v0.9.26 is a large catch-up release: its fixes landed over the summer and were
first released then. Nothing has been tagged since 31 October. Work that is
only on `dev` is at the end, under "In progress".

## Verdicts that say only what was measured

- Coverage from the unit lane now reaches the verdict, and verdicts match plan
  subtasks by id and then by test file, so a paraphrased id no longer leaves a
  fabricated judge score of `0`
  ([v0.9.26](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.26),
  [#1296](https://github.com/olafkfreund/TFactory/pull/1296)).
- Handback and triage text labels each reason **Measured** or **LLM-authored,
  not verified**
  ([v0.9.26](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.26),
  [#1298](https://github.com/olafkfreund/TFactory/pull/1298)).
- A run that never reached the code under test (command not found, exit
  126/127, environment failure) is `not_run`, not a test failure
  ([v0.9.29](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.29),
  [#1348](https://github.com/olafkfreund/TFactory/pull/1348)). The same applies
  to a mutant that never ran
  ([v0.9.35](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.35),
  [#1402](https://github.com/olafkfreund/TFactory/pull/1402)).
- Batched Nix pytest lanes keep `junit.xml` and `coverage.xml`, so coverage is
  measured instead of reading 0
  ([v0.9.36](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.36),
  [#1408](https://github.com/olafkfreund/TFactory/pull/1408)).

## Kotlin and Java run in-cluster

- Kotlin unit tests run through Gradle in a per-task Nix Job
  ([v0.9.27](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.27),
  [#1310](https://github.com/olafkfreund/TFactory/pull/1310)), and the Planner
  can emit Kotlin subtasks
  ([v0.9.28](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.28),
  [#1322](https://github.com/olafkfreund/TFactory/pull/1322)).
- Java unit tests run through Maven, and Gradle-only projects run `gradle test`
  ([v0.9.29](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.29),
  [#1333](https://github.com/olafkfreund/TFactory/pull/1333)). JVM tests are
  graded per class
  ([v0.9.32](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.32),
  [#1383](https://github.com/olafkfreund/TFactory/pull/1383)).
- Swift is refused with "no in-cluster Swift lane" rather than looking
  supported
  ([v0.9.32](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.32),
  [#1389](https://github.com/olafkfreund/TFactory/pull/1389)).

## Reproducible, isolated verification

- Spec ingest takes an optional `ref` (branch, tag or full SHA) and verifies a
  detached per-run worktree. `source_ref` and `source_sha` are recorded
  ([v0.9.38](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.38),
  [#1416](https://github.com/olafkfreund/TFactory/issues/1416)).
- A git-tracked `flake.nix` puts a contract-less run on Nix in the repo's own
  devShell
  ([v0.9.35](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.35),
  [#1403](https://github.com/olafkfreund/TFactory/pull/1403)).
- Verify always packs its workspace through the object store, and a pack
  failure records `not_run`
  ([v0.9.33](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.33),
  [#1392](https://github.com/olafkfreund/TFactory/pull/1392)).

## Hand-offs with the rest of the line

- Every terminal verify status sends the completion event to CFactory
  ([v0.9.30](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.30),
  [#1372](https://github.com/olafkfreund/TFactory/pull/1372)).
- `GET /api/tfactory/tasks/{spec_id}` returns `source_sha`, so AIFactory's
  merge gate can tell a verdict for an older commit from the PR head
  ([v0.9.31](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.31),
  [#1378](https://github.com/olafkfreund/TFactory/pull/1378)).
- Spec ingest validates the task contract against the vendored schema: warn
  mode in [v0.9.41](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.41)
  ([#1433](https://github.com/olafkfreund/TFactory/pull/1433)), enforce by
  default from [v0.9.43](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.43)
  ([#1442](https://github.com/olafkfreund/TFactory/pull/1442)).
- Ingest checks PFactory's Ed25519 contract signature in **warn mode** by
  default. With no keys configured, every contract warns "unsigned" and still
  ingests
  ([v0.9.44](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.44),
  [#1444](https://github.com/olafkfreund/TFactory/pull/1444)).
- Handback sending to AIFactory stays **off by default**
  (`TFACTORY_HANDBACK_SEND`)
  ([v0.9.44](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.44),
  [#1446](https://github.com/olafkfreund/TFactory/pull/1446)).

## Access control, mostly in shadow mode

- Role checks on task-keyed write routes (20 in the release notes) run in
  **shadow mode** behind `RBAC_ENFORCE_TASK_ROUTES`, which is off by default:
  would-be denials are logged and audited, not blocked. The notes say not to
  enable it before RBAC slice S6, because projects have no `org_id` yet
  ([v0.9.46](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.46),
  [#1457](https://github.com/olafkfreund/TFactory/pull/1457)).
- Remote MCP authorizes the calling key on each tool call, and remote MCP
  stays off by default (`TFACTORY_MCP_REMOTE_ENABLED`)
  ([v0.9.47](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.47),
  [#1470](https://github.com/olafkfreund/TFactory/pull/1470)).
- OIDC ID-token claims (`iss`, `aud`, `exp`, algorithm) are enforced
  ([v0.9.47](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.47),
  [#1464](https://github.com/olafkfreund/TFactory/pull/1464)), and
  `GET /api/audit/export` needs org admin
  ([v0.9.45](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.45),
  [#1452](https://github.com/olafkfreund/TFactory/pull/1452)).
- Several worktree routes now need `project_id:spec_id`, and a bare spec id no
  longer works, which is a behaviour change
  ([v0.9.47](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.47),
  [#1472](https://github.com/olafkfreund/TFactory/pull/1472)).

## Supply chain and code scanning

- The runner images are signed keyless with cosign and self-verified
  ([v0.9.26](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.26)).
- A weekly CodeQL ratchet fails when open critical, high, error or medium
  alerts exceed a baseline of 0
  ([v0.9.41](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.41),
  [#1426](https://github.com/olafkfreund/TFactory/pull/1426)).
- Open-in-IDE no longer accepts a custom program, and project browsing is
  confined to `$HOME` and `APP_FILE_BROWSE_ROOTS`
  ([v0.9.40](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.40),
  [#1424](https://github.com/olafkfreund/TFactory/pull/1424)).
- CVE-gate fixes that failed the Trivy P0 gate, most recently uv
  ([v0.9.49](https://github.com/olafkfreund/TFactory/releases/tag/v0.9.49),
  [#1486](https://github.com/olafkfreund/TFactory/pull/1486)).

## In progress

Merged to `dev` and in no release. Only the PR titles were read, so behaviour
beyond the title is not claimed:

- Boot migrations serialised with a Postgres advisory lock
  ([#1491](https://github.com/olafkfreund/TFactory/pull/1491)).
- A Helm hook Job for `migrations.autoApply=false`
  ([#1493](https://github.com/olafkfreund/TFactory/pull/1493)).
- Scoped `acw_` API keys accepted on WebSocket
  ([#1496](https://github.com/olafkfreund/TFactory/pull/1496)), and `/ws/events`
  confined to the caller's orgs
  ([#1500](https://github.com/olafkfreund/TFactory/pull/1500)).
- An allowlisted environment for agent child processes
  ([#1501](https://github.com/olafkfreund/TFactory/pull/1501)).

The full list is on the [release page](https://github.com/olafkfreund/TFactory/releases).

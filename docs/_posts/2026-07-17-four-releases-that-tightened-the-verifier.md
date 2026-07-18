---
layout: post
title: "Four releases that tightened the verifier itself"
subtitle: "0.9.8 through 0.9.11 in one day: the contract schema stops drifting, the web-server suite stops rotting, a whole flake class dies, a trust boundary gets a name, VAL-3 runs on a real cluster, and the verify lane finally follows the deliverable."
date: 2026-07-17 12:00:00 +0000
author: DataSeek Team
---

TFactory's job is to hold other people's code to a standard, which makes it
embarrassing when its own scaffolding slips. This cycle shipped four releases in
a day — 0.9.8 through 0.9.11 — and almost all of it points inward: the
verifier's own contracts, its own test suite, its own file writes, its own
security boundaries. Here is what landed and why each piece matters.

## 0.9.8 — the schema stops drifting, and the web-server suite stops rotting

The vendored task-contract-v2 schema is what ingest validates every incoming
contract against. It turned out to be a fossil: 623 lines behind the canonical
hub copy, missing `execution.autonomy_tier`, `routing`, `deployment`,
`environment`, and the entire `$defs` block (#679). Contract validation was
running against a version of the contract that no longer existed. The copy is
now byte-synced to the hub canonical, and a blocking CI drift gate
(`scripts/check_schema_drift.py`, reused from PFactory's proven gate) makes the
stale state unrepresentable: hard fail on drift, soft skip only when the network
is down.

The same release fixed a quieter rot. CI ran only `pytest tests/`, so the
`apps/web-server` suite decayed to 31 failures without anyone noticing (#681).
The suite was triaged honestly — fixed where behaviour had changed, deleted
where the behaviour under test had been removed — and wired into `ci.yml` as a
blocking step, green in its own introduction run. A test suite that does not
run in CI is not a test suite; it is a rumour.

## 0.9.9 — atomic secret writes, a named trust boundary, and VAL-3 for real

Three things in one release, all of them about closing gaps between what the
code claimed and what it did.

First, `write_secret_file` is now atomic (#688). The old path wrote in place,
so a concurrent reader could observe a torn hybrid of old and new content —
the root cause of the flaky `TestFileLocking` CI failures. The fix is the
boring, correct one: `mkstemp` in the same directory plus `os.replace`. The
stress evidence is the point: 65 torn files in 1,000 iterations before, 0 in
1,000 after. That is a flake class removed structurally, not retried into
submission.

Second, CodeQL alerts #705-#709 were not sanitizer misses — they were flows of
an untrusted project path, a trust boundary the code enforced but had never
named (#664). A new `trusted_project_root()` choke point gives the boundary a
name and a barrier, and the local CodeQL oracle (2.25.6) confirms the claim:
21 residual flows down to 17, zero in the terminal-worktree service, and no
over-suppression.

Third, the VAL-3 disposable-target provisioner (#607). VAL-3 means effectful
behaviour verified against a real, disposable host — never production. The new
k8s-Job backend runs each effectful command as an ephemeral Kubernetes Job
(create, watch, collect logs, delete), env-gated and off by default, with no
credentials in the Job env or argv and teardown on every failure path. It was
then proven live for the Factory#257 milestone: a real Job scheduled onto a
different node of the factory cluster, executed, and fully torn down. Until
this release VAL-3 was an honest `not_run`; now it is a level the fleet can
actually earn.

## 0.9.10 — tenant scoping, behind a flag

Verification specs, runs, and verdicts are now tenant-scoped (#683). Ingest
accepts an optional `tenant` field (an explicit AIFactory stamp always wins),
or resolves the `X-Tenant-Id` header when `TFACTORY_MULTI_TENANT` is on. The
tenant is written into the spec workspace and the task list filters by it. With
the flag off — the default — behaviour is byte-identical apart from the new
field, and readers lazily backfill legacy rows to `"default"`. Part of the
fleet-wide multi-tenancy program.

## 0.9.11 — the verify lane follows the deliverable, not the repo

The best bug of the batch. On a repo carrying a `go.mod` left over from earlier
polyglot runs, a pure-Python deliverable got Go tests generated — nine of nine
failing at compilation — because the planner's language detection ranked
acceptance-criteria command tokens first and repo manifests second, and when
neither was decisive it guessed from repo markers (#696). The repo is not the
deliverable.

Detection now ranks deterministic signals strongest-first: the extensions of
the files actually changed on the ingest `source_branch` diff, then the
deliverable filenames named in the spec text, then AC command tokens, and repo
manifests only as an unambiguous last resort. A mixed diff never guesses — it
falls through to the next signal. Regression tests pin both directions: a
Python diff on a Go-marker repo selects the Python lane, and a Go diff on a
Python repo still selects Go.

## The through-line

None of these releases added a feature a demo would show off. They made the
verifier harder to fool and harder to break: contracts that cannot silently
drift, tests that cannot silently rot, writes that cannot tear, boundaries with
names and proofs, an assurance level that is earned on a real cluster, and a
planner that tests what was actually delivered. That is the job.

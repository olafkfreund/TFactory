---
status: draft
issue: 1511
author: olafkfreund
---

# Intent: TFactory LOW findings from the wave-8 cross-PR review

## Problem

Three LOW findings (TFactory PRs #1511, #1517, #1513), unverified end to end:

1. `tools/git_writer.py` runs every git child under the agent allowlist env, which drops `GNUPGHOME`, `GPG_TTY`, `GPG_AGENT_INFO`. With `TFACTORY_TRIAGER_GIT_SIGN=1` signing then fails.
2. `agents/regression/notify.py` posts `merge_sha = run.commit` for whatever task's `source.json` is in `spec_dir`; nothing ties that commit to that task's merge.
3. `POST /api/specs` accepts `originator` / `agent_identity` as unbounded free strings that go into `source.json` and the completion event.

## Proposed outcome

1. Signing variables reach git children only when signing is on.
2. Confirmed or refuted against AIFactory's receiving side; no wrong revert is possible.
3. Both fields are length-capped and charset-limited at ingest.

## Affected users and systems

TFactory backend and web-server; AIFactory `post-merge-regression` route (read only).

## Constraints

No behaviour change for unsigned runs. Existing valid values (`github:octocat`, `agent:aifactory/<key id>`) stay accepted. No contract change with AIFactory.

## Open questions

None. Findings 1 and 3 are confirmed; finding 2 is checked against `origin/dev` of AIFactory in the spec.

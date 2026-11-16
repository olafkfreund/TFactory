---
status: approved
issue: 1511
intent: intent/2026-11-16-review-low-findings-tfactory.md
---

# Spec: TFactory LOW findings

## Design

1. Confirmed: `_run` builds env with `git_env(keep=...)`, and `git_env` drops the GPG variables. In `_run`, when `_sign_enabled()`, add `GNUPGHOME`, `GPG_TTY`, `GPG_AGENT_INFO`, `SSH_AUTH_SOCK` (ssh signing) to `keep`. Nothing else is added; off means unchanged (`SSH_AUTH_SOCK` already passes for remote git via `_REMOTE_KEEP`). Codex review: (a) ok; (c) trailing newline test added, pydantic v2 `$` is strict end-of-input.
2. Refuted as a revert risk, no code change. AIFactory `server/services/revert_pr.py` (origin/dev) only acts on a commit it recorded itself when its pipeline merged the PR: `_find_record` needs a recorded sha, `_refusal` requires the record's project and spec to equal the task in the URL, `_confirmed` requires GitHub to still show that PR merged at that commit. A wrong sha gets 409 `not_pipeline_merged`. The source.json envelope carries no merge sha, so TFactory cannot tie one; refusing to send would disable the feature. Left as is.
3. Confirmed: add `max_length=128` and a charset pattern `^[\w.:@/+\- ]+$` to both fields on `SpecIngestRequest`. No schema is defined in factory-contracts for these fields, so the cap and charset are chosen to fit `github:<login>` and `agent:<service>/<key id>`.

## Alternatives rejected

- Pass the whole environment when signing: widens the leak surface.
- Refuse the notify without a recorded merge sha: kills the feature, AIFactory already guards.
- Enum for originator: values are open-ended.

## Risks

A sender using characters outside the set gets a 422 on ingest; known senders do not.

## Verification

New tests: signing env present only when on; provenance validation rejects long, newline, quote and empty values. Existing git_writer, ingest, completion and notify tests still pass; ruff clean.

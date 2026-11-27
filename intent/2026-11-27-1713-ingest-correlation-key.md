---
status: approved
issue: 1713
author: olafkfreund
---

# Intent: a spec ingest can carry the run's correlation key

## Problem

`factory test` asks TFactory to verify a merged SHA through `POST /api/mcp-stdio/specs/ingest`.
The request has no field for the run's correlation key. Sending a `contract` just to carry one
would change `_repo_binding`. So the verify completion files under the synthetic
`tf-<project>:<spec>` key, apart from PFactory's and AIFactory's work item for the same run. E2E
run `mvr-20261127u` (Factory#3939) showed this.

## Proposed outcome

The ingest accepts an optional `correlation_key`. TFactory records it in `context/source.json`,
where the triager already looks before minting a synthetic key. The verify completion then
joins the run's CFactory work item.

## Affected users and systems

- TFactory web-server: `routes/specs.py` (`SpecIngestRequest`, `ingest_spec`).
- TFactory backend: `agents/tools_pkg/tools/task_control.py` (`create_spec_ingest_workspace`).
- Callers: the Factory CLI `factory test`, which is a separate change under Factory#3939.

## Constraints

- **Optional and validated:** at most 128 characters, a safe character set, and never a bare
  number (#1467).
- **The handoff key wins:** when both are present, a handoff contract's own `correlation_key`
  takes precedence.
- **Nothing else changes:** omitting the field leaves behaviour as it is today.

## Open questions

None.

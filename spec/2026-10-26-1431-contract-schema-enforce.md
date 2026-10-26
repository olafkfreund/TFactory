---
status: approved
issue: 1431
intent: intent/2026-10-26-1431-contract-schema-enforce.md
---

# Spec: enforce the task-contract schema at ingest by default (#1431)

## Design

The user's decision: flip the default to `enforce`. In
`apps/backend/agents/tools_pkg/tools/task_control.py` the mode read changes from
`os.environ.get("TFACTORY_CONTRACT_SCHEMA_MODE", "warn")` to `"enforce"`. The
existing branch logic stays: `warn` logs and continues, anything else rejects.
Docstring, `docs/capabilities-and-usage.md` and the CHANGELOG say enforce is the
default and warn is the opt-out. Tests: the "warned by default" test becomes
"rejected by default" (pins the new default), and a new test covers explicit
`warn` still storing the contract with a warning.

## Alternatives rejected

- Keep `warn` and set `enforce` in gitops: the user chose the code default so
  every deployment is protected, not only the cluster.
- Remove the `warn` mode entirely: it is the rollback lever without a release.

## Risks

A sender still posting an invalid contract now gets a `400` on the cluster after
the next release. #1438 found no code sender; hand-made ingests use the
template. Rollback: set `TFACTORY_CONTRACT_SCHEMA_MODE=warn` and restart.

## Verification

`pytest tests/test_spec_ingest_workspace.py`; the new default test fails on the
old code. Full critical lane via the pre-commit hook. CI green.

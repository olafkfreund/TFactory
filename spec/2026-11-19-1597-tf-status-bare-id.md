---
status: draft
issue: 1597
intent: intent/2026-11-19-1597-tf-status-bare-id.md
---

# Spec: Task status identity guard

## Design

execution.py `_ingest_status` takes the requested spec id and returns {} when the loaded status.json carries a string task_id or spec_id that differs from it (absent keys are accepted, so old files still work). The bare-id lookup stays an exact literal join with the exactly-one-match rule. The colon form uses the same guard with its spec part. The MCP proxy shares get_task_status so is covered.

## Alternatives rejected

Prefix or latest-spec lookups: not needed, ids are exact. Reading Postgres job rows: owner decision on #1597 was status.json.

## Risks

A status.json legitimately written with a different task_id (renamed spec) would show no phase; none such is created by create_spec_ingest_workspace, which writes task_id = spec_id (task_control.py:968).

## Verification

tests/test_task_status_ingest.py: two concurrent tasks in one project, each id returns its own phase; a status.json whose task_id names the other task returns no phase for the requested id. The second fails on current dev.

---
status: approved
issue: 1515
intent: intent/2026-11-16-review-fixes-tfactory.md
---

# Spec: three review defects

## Design

1. `agents/verify_dispatch.py`: forward the three named vars and every `TFACTORY_GUARDRAIL_*` var when set (same pattern as the #719/#1012 blocks; never invented when unset).
2. `agents/gen_functional.py`: `locked = code_values(draft) & criterion_literals(_criterion_text(subtask))`. Guessed literals are free for wiring; the criterion's values stay locked (and `check_criterion_literals` still guards them).
3. `server/utils/project_env.py`: URL redirector keys (`OLLAMA_BASE_URL`, `OLLAMA_CLOUD_BASE_URL`, `OPENAI_COMPATIBLE_BASE_URL`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_BASE_URL`, `GIT_BASE_URL`) load only if the host env held none of the credentials that provider uses (incl. the `OPENAI_API_KEY` fallback) before the file was read. `GRAPHITI_MCP_URL` leaves the allowlist (no UI writes it, no credential to condition on).

## Alternatives rejected

- Prefix-forward all `TFACTORY_*` to the Job: leaks control-plane secrets/flags.
- Drop URL keys from the allowlist entirely: breaks the settings UI.
- Scrub host credentials in the runner env: wider change, other callers.

## Risks

Item 3: a host that holds a key and relied on a project URL now ignores it (logged by key name); the host must set the URL. Item 2: a draft that dropped a criterion value in step 1 is no longer locked on it; #888 still catches that.

## Verification

One regression test per item, each failing before the fix: `tests/test_verify_dispatch.py`, `tests/test_gen_functional_two_step.py`, `apps/web-server/tests/test_project_env_allowlist.py`.

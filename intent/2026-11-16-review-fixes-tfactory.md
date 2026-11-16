---
status: approved
issue: 1515
author: olafkfreund
---

# Intent: three review defects (#1515, #1516, #1506)

## Problem

1. #1515: the verify Job env is a curated allowlist; `TFACTORY_ACT_GUARDRAIL`, `TFACTORY_RUN_TOKEN_CEILING`, `TFACTORY_MAX_TURNS` and `TFACTORY_GUARDRAIL_*` never reach it, so operator overrides are silently ignored where the agent runs.
2. #1516: the two-step generator locks every numeric constant of the assertion draft (guessed ids, ports, timeouts), so correct wiring that fixes one is rejected and replanned.
3. #1506: the repo-controlled `.tfactory/.env` may set endpoint URLs, so a committed URL sends a host-held API key or token to an attacker, and `GRAPHITI_MCP_URL` points the MCP client at an attacker server.

## Proposed outcome

Overrides reach the Job; only the criterion's own values are locked; a project `.env` cannot redirect a credential the host holds.

## Affected users and systems

Operators of kubejob verify; the test generator; web-server runners (agent_service, pr_review_service).

## Constraints

The settings UI still writes `OLLAMA_BASE_URL`, `AZURE_OPENAI_ENDPOINT`, `GIT_BASE_URL` to the project `.env`; those keep working when the host holds no matching credential. No cluster changes.

## Open questions

None.

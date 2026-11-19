---
status: approved
issue: 1609
author: olafkfreund
---

# Intent: Serve the node app nearest the spec targets

## Problem

detect_serve_command returns the repo root `npm start` for a node app in a subdirectory (aifactory-demo scoreboard/server.js), which is the repo's python uvicorn command, so the browser lane gets ERR_CONNECTION_REFUSED.

## Proposed outcome

A spec whose targets sit under a directory holding server.js/index.js serves `PORT=<port> node <dir>/<entry>`.

## Affected users and systems

TFactory verify pipeline (web-server routes and backend agents); callers: cockpit, MCP proxy, spec-ingest verify runs.

## Constraints

Smallest correct change; tests that fail without the fix; no live-cluster action; repo-owned flakes and contracts keep winning.

## Open questions

None. Decided under the owner delegation of 2026-11-19 (recommended option).

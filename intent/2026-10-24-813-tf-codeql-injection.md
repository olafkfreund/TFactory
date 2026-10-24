---
status: approved
issue: Factory#813
author: olafkfreund
---

# Intent: Close TFactory's real command- and path-injection CodeQL alerts

## Problem

Four open CodeQL alerts on TFactory `dev` are real or near-real:

- **1853, 1854** (`py/command-line-injection`, `routes/worktree_tools.py:262,302`).
  `POST /tasks/worktree/open-in-ide` and `/open-in-terminal` take `customPath`
  from the request body and make it `argv[0]` (`return [custom_path, path]`).
  `_safe_arg` only blocks shell metacharacters, so any API token holder can make
  the server run any binary on the host. Remote code execution.
- **1892** (`py/path-injection-sanitized`, `routes/files.py:239`).
  `GET /files/discover` resolves the raw `base_path` query parameter and
  `iterdir()`s it, so any directory on the server can be listed.
- **1893** (`py/path-injection-sanitized`, `routes/git.py:1308`). The worktree
  name check uses `re.match(r"^[a-zA-Z0-9_-]+$")`; `$` also matches before a
  trailing newline. Not exploitable today (the name is `.strip()`ed first), but
  the analyser cannot see that.

AIFactory fixed the same code already (#1267, #320, #1353).

## Proposed outcome

- A request carrying a non-empty `customPath` gets HTTP 400 with a reason and
  no process is started. The `ide` / `terminal` name tables still work.
- `/files/discover` lists only directories under a registered project, `$HOME`,
  or an operator-listed root; anything else gets HTTP 403.
- The worktree-name check is a full match.
- The four alerts close on the next CodeQL run on `dev`.

## Affected users and systems

- TFactory web-server: `routes/worktree_tools.py`, `routes/files.py`,
  `routes/git.py`, `routes/_specpath.py`, the CodeQL custom query pack.
- TFactory web frontend: the open-in-IDE/terminal callers and the Add Project
  scan default.
- Users who set a custom IDE/terminal path lose that option (by design).

## Constraints

- Port AIFactory's code; do not invent a new design.
- Smallest change; no new dependency.
- Must not break the Add Project scan for the default case.
- One PR into `dev`. Tests for every new check.

## Open questions

None. (Pre-approved for this sprint.)

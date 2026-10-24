---
status: draft
issue: Factory#813
intent: intent/2026-10-24-813-tf-codeql-injection.md
---

# Spec: Close TFactory's real command- and path-injection CodeQL alerts

## Design

All paths below are under `apps/web-server/server/` unless stated.

### D1. `customPath` is refused, not validated (alerts 1853, 1854)

Port AIFactory #1267 (`AIFactory origin/dev routes/worktree_tools.py:36-80`):

- Add `CUSTOM_PATH_DISABLED` (the reason string) and
  `reject_custom_path(custom_path)` that raises `HTTPException(400,
  CUSTOM_PATH_DISABLED)` when the value is non-empty.
- Keep `customPath` in `OpenInIDERequest` / `OpenInTerminalRequest` as
  `Field(default=None, description=CUSTOM_PATH_DISABLED)`. Dropping the field
  would make pydantic ignore it and silently launch a different program.
- Call `reject_custom_path(request.customPath)` as the first line of
  `open_worktree_in_ide` and `open_worktree_in_terminal`, before any path work.
- Remove the `custom_path` parameter and branch from `get_ide_command` and
  `get_terminal_command`. The program always comes from the fixed tables.
  `tasks_worktree.py:2415` only re-exports the names; it needs no change.
- Keep TFactory's existing `_validated_worktree_path` + `_safe_arg` for the
  worktree path. We do not port AIFactory's `resolve_launch_dir` rewrite.

**CodeQL twin (needed to actually close the alert).** Stock
`py/command-line-injection` treats the whole `cmd` variable passed to
`Popen(cmd, ...)` as the command when `cmd` is not a list literal at the call
site, and taint flows from `worktreePath` into the list. So removing
`customPath` alone may leave 1853/1854 open on the worktree path. Copy
AIFactory's `.github/codeql/custom-queries/CommandInjectionSanitized.ql`,
with `_safe_arg` as the barrier (call result and its first parameter), and
exclude stock `py/command-line-injection` in `.github/codeql/codeql-config.yml`.
That is the same replace-with-twin pattern the config already uses for
path-injection and partial-ssrf, and the config's own rule says an exclude
needs a twin. `_safe_arg` is an honest barrier once `customPath` is gone: it
only sees the worktree path, which is already confined to a registered project
and resolved to an absolute path, so it can't pick the program or start with `-`.

**Frontend.** `apps/frontend-web/src/lib/api-adapter.ts:423-426` and
`shared/types/ipc.ts:274-275` drop the `customPath` argument. The four call
sites (`components/Terminal.tsx:230`, `task-detail/TaskFiles.tsx:226`,
`task-review/WorkspaceStatus.tsx:174,201`) stop passing
`settings.customIDEPath` / `customTerminalPath` and drop them from their hook
dependency arrays. Without this, a user who once set a custom path would get
a 400 on every open, even with a named IDE.

### D2. `/files/discover` is confined to browse roots (alert 1892)

Port into `routes/_specpath.py` from AIFactory `server/specpath.py`:
`contained_path` (line 75), `within_roots` (:139) and `browse_roots` (:191).
Also add a local `registered_project_roots()` that reads
`routes.projects.load_projects` **inside the function** (late binding for tests;
the same function-local import `worktree_tools._validated_worktree_path`
already uses). Behaviour:

- `contained_path(candidate, roots, what, expand_user=False)`: resolve, return it
  if it equals a root or sits under one, else `ValueError`.
- `within_roots(...)`: the same, but raises HTTP 403
  `"Access denied: path is outside the <what>"` `from None`, so the rejected
  path never reaches the client.
- `browse_roots()`: registered project paths + `Path.home()` + every entry of
  `APP_FILE_BROWSE_ROOTS` (`os.pathsep` list). Bad entries are skipped, not fatal.

`routes/files.py:239` becomes
`base = within_roots(base_path, browse_roots(), "browsable directories", expand_user=True)`.
`expand_user=True` keeps today's `~` support (the current code calls
`expanduser()`), and does the expansion inside the barrier.

**Default scan folder.** `apps/frontend-web/src/components/AddProjectModal.tsx:37`
`DEFAULT_PROJECTS_FOLDER = '/home'` is the parent of `$HOME`, so it would get
403 on every first open. Change it to `'~'`, which expands to the server's home,
a browse root.

**CodeQL.** Add `contained_path` and `within_roots` to the barrier list in
`.github/codeql/custom-queries/PathInjectionSanitized.ql:46-49`, and add their
first parameter to the parameter-barrier clause (`:59-62`), the same way
AIFactory's query does. Without that, the `resolve()` inside the helper fires
the alert again.

**Docs.** One commented line for `APP_FILE_BROWSE_ROOTS` in `.env.example` next
to the other `APP_*` entries, so operators can find the escape hatch.

### D3. Worktree name is a full match (alert 1893)

`routes/git.py:1230`: `re.match(r"^[a-zA-Z0-9_-]+$", name)` →
`re.fullmatch(r"[a-zA-Z0-9_-]+", name)`. Same as AIFactory #1353.

## Alternatives rejected

- **Validate `customPath` harder** (allowlist binaries, `shutil.which`): the
  caller still picks the program. An operator-side allowlist is AIFactory
  #1267's follow-up, not this fix.
- **Drop the `customPath` field from the models**: silent substitution, see D1.
- **Port AIFactory's whole `worktree_tools.py`** (`resolve_launch_dir`,
  `assert_not_option`, `Popen(cwd=)`): bigger diff, and it pulls in
  `services/argv_safety.py`. TFactory's existing confinement already does the
  job for the path.
- **Barrier `_safe_arg` while `customPath` still exists**: it would hide the
  real RCE. The twin is only honest once D1 has removed `customPath`.
- **Confine discover to registered projects only**: then the first project
  could never be added.
- **Keep `'/home'` as default and add `/home` as a root**: lets anyone list every
  user's home directory.
- **Also confine `/files/list` and `/files/read`** (`files.py:316,371` share the
  pattern): no open alert names them, so they are out of scope. Follow-up issue.

## Risks

- Users who set a custom IDE/terminal path lose it. If they pick the "custom"
  IDE in settings, the server falls back to VS Code (`get_ide_command`'s
  default). The settings UI fields stay for now (follow-up). Accepted, matches
  AIFactory.
- Deployments whose projects live outside `$HOME` and are not registered yet
  need `APP_FILE_BROWSE_ROOTS` to scan them. The container image keeps projects
  under `/home/projects` and runs with `HOME=/home/nonroot`, so the default
  container scan of `/home/projects` needs the env var. Documented in
  `.env.example`; the 403 message says why.
- The new CodeQL twin could fail to compile. The PR's CodeQL check shows that.
- 1893 might stay open if the analyser also taints `worktrees_base`. Then it's
  a separate finding; note it on the PR, do not widen this change.

## Verification

- `apps/web-server/tests/test_worktree_tools_custom_path.py` (new): POST to
  each endpoint with `customPath` set gives 400 with `CUSTOM_PATH_DISABLED`
  and `subprocess.Popen` is never called. Without `customPath`, the 400 is not
  raised (the request moves on to path validation).
- `apps/web-server/tests/test_specpath.py` (extend): `within_roots` returns the
  resolved path inside a root, gives 403 outside it and for a `..` escape, and
  `browse_roots` includes `APP_FILE_BROWSE_ROOTS` entries and the registered
  project paths.
- `apps/web-server/tests/test_files_discover_roots.py` (new): `/discover` outside
  the roots gives 403 and never lists; inside an `APP_FILE_BROWSE_ROOTS` tmp dir
  it returns the fixture project.
- `npm run typecheck` in `apps/frontend-web` passes.
- The PR's CodeQL run reports 1853, 1854, 1892 and 1893 as fixed, with no
  new alerts in the touched files.
- No test for D3: the name is stripped first, so `match` and `fullmatch`
  behave the same on every reachable input.

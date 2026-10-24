---
status: draft
issue: Factory#813
spec: spec/2026-10-24-813-tf-codeql-injection.md
---

# Plan: Close TFactory's real command- and path-injection CodeQL alerts

Branch `fix/813-tf-codeql-injection` off `origin/dev`. One PR into `dev`.
Worktree: `/tmp/claude-1000/-mnt-code-Source-home-GitHub-Factory/8787aafd-2d97-443b-ae9a-8faa12f38be2/scratchpad/lanes/tf-codeql`.
Paths are relative to the worktree. `WS` = `apps/web-server/server`.
Reference repo: `/mnt/code/Source-home/GitHub/AIFactory`, read it with
`git -C <it> show origin/dev:<path>` (never check out there).

## Approved decisions (from the spec)

- **D1** `customPath` gets HTTP 400 with `CUSTOM_PATH_DISABLED` before any
  other work. The field stays in both request models
  (`Field(default=None, description=CUSTOM_PATH_DISABLED)`) so it is not silently
  dropped. Command builders lose the `custom_path` parameter. Keep TFactory's
  `_validated_worktree_path` + `_safe_arg`. Do not port `resolve_launch_dir` /
  `argv_safety`.
- **D1-ql** Add a `CommandInjectionSanitized.ql` twin with `_safe_arg` as the
  barrier, and exclude stock `py/command-line-injection`. This is honest only
  because D1 removes `customPath`. Never land the twin without D1.
- **D1-fe** The frontend stops sending `customPath`.
- **D2** Port `contained_path` / `within_roots` / `browse_roots` (plus a local
  `registered_project_roots`) into `WS/routes/_specpath.py`. `/files/discover`
  calls `within_roots(..., expand_user=True)`, and anything outside the roots
  gets 403. Roots = registered projects + `$HOME` + `APP_FILE_BROWSE_ROOTS`.
  The Add Project default folder changes from `'/home'` to `'~'`. Register both
  helpers as path barriers in the ql. Add a one-line `.env.example` entry.
- **D3** `git.py` worktree name: `re.fullmatch`.
- Out of scope: `/files/list`, `/files/read` (same pattern, no alert), the
  settings UI custom-path fields, xterm's `cd {path} && $SHELL` argv. Mention
  them in the PR as follow-ups.

## Steps

1. `WS/routes/worktree_tools.py:17-18,69-83`: import `Field` from pydantic. Add
   `CUSTOM_PATH_DISABLED` and `reject_custom_path()`, copied word for word from
   AIFactory `origin/dev apps/web-server/server/routes/worktree_tools.py:42-74`
   (reword "#1267" to "AIFactory#1267"). Change both models' `customPath` to
   `str | None = Field(default=None, description=CUSTOM_PATH_DISABLED)`, keeping
   the AIFactory comment on why the field stays.
   → verify: `python -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" apps/web-server/server/routes/worktree_tools.py`
   Traps: `HTTPException` is already imported (line 17), so don't add it twice.

2. `WS/routes/worktree_tools.py:85-93,170-186`: drop the `custom_path` parameter
   and the `if custom_path:` branches from `get_ide_command` and
   `get_terminal_command`. Add one docstring line: the program always comes from
   the table, never the request (AIFactory#1267).
   `WS/routes/worktree_tools.py:240-310`: in both handlers, put
   `reject_custom_path(request.customPath)` as the FIRST statement (before
   `_validated_worktree_path`). Delete the `custom_path = ...` lines (251, 291).
   Call `get_ide_command(ide, worktree_path)` / `get_terminal_command(terminal,
   worktree_path)`. In the `_safe_arg` docstring (56-66), change "(the worktree
   path and any custom executable path)" to "(the worktree path)".
   → verify: `grep -n "custom_path" apps/web-server/server/routes/worktree_tools.py` prints nothing.
   Traps: `tasks_worktree.py:2415-2423` re-exports these names. Keep the names,
   don't touch that file. The reject must come before path validation, or a
   bad path would return 400 "not within a known project" and hide the reason.

3. `apps/web-server/tests/test_worktree_tools_custom_path.py` (new): build
   `FastAPI()`, `include_router(worktree_tools.router)` and use `TestClient`.
   Monkeypatch `worktree_tools.subprocess.Popen` with a stub that records calls.
   Tests: (a) `/worktree/open-in-ide` with `customPath="/bin/sh"` gives 400,
   `detail == CUSTOM_PATH_DISABLED`, and Popen is not called; (b) the same for
   `/worktree/open-in-terminal`; (c) `customPath` omitted gives no
   `CUSTOM_PATH_DISABLED` (monkeypatch `server.routes.projects.load_projects` to
   `{}`, so it gives 400 "Path is not within a known project" instead, which
   proves the reject didn't fire); (d) `get_ide_command("vscode", "/p") ==
   ["code", "/p"]`.
   → verify: `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest apps/web-server/tests/test_worktree_tools_custom_path.py -q`, all pass.
   Then mutation check: temporarily comment out one `reject_custom_path` call,
   confirm (a) or (b) fails, then restore it.
   Traps: `apps/web-server/tests/conftest.py` already puts `apps/web-server` on
   `sys.path`, so import with `from server.routes import worktree_tools`. The
   worktree has no `.venv`, so use the main clone's venv path above and don't
   create one. If it is missing an import, run it under `nix develop -c`
   (see `Justfile:65-76`).

4. `.github/codeql/custom-queries/CommandInjectionSanitized.ql` (new): copy
   AIFactory `origin/dev:.github/codeql/custom-queries/CommandInjectionSanitized.ql`
   with its metadata header (`@id py/command-line-injection-sanitized`). Set the
   barrier name list to `["_safe_arg"]`, and the parameter-barrier `f.getName()
   in ["_safe_arg"]`. Rewrite the header comment for TFactory: `_safe_arg` only
   sees the confined worktree path now, because `customPath` is refused (Factory#813).
   `.github/codeql/codeql-config.yml:38-42`: add
   `- exclude:\n      id: py/command-line-injection` under `query-filters`, and add
   one sentence to the header comment (lines 18-32 style) naming the twin.
   → verify: `grep -n "command-line-injection" .github/codeql/codeql-config.yml .github/codeql/custom-queries/CommandInjectionSanitized.ql`
   shows the exclude and the `@id`. If you can, compile it:
   `nix shell nixpkgs#codeql -c codeql query compile --search-path ~/.codeql/packages .github/codeql/custom-queries/CommandInjectionSanitized.ql`
   (optional; the PR CodeQL run is the real check).
   Traps: the config's rule is "an exclude without a twin is not allowed", so
   both files go in the same commit. Do not barrier `_validated_worktree_path`
   or `safe_join` for command injection. One barrier is enough.

5. `WS/routes/_specpath.py` (append after `safe_join`, ~line 92): add
   `import contextlib` and `from collections.abc import Iterable`. Port
   `contained_path` (AIFactory `server/specpath.py:75`, ~45 lines including
   docstring, shorten it) and `within_roots` (`:139`). In `within_roots`, use the
   module-level `HTTPException` (already imported, line 24) and `status=403`;
   keep `from None`. Add `registered_project_roots()` with a function-local
   `from .projects import load_projects` (late binding, as at
   `worktree_tools.py:43`) and the same `contextlib.suppress(OSError, KeyError,
   TypeError)` loop. Port `browse_roots()` (`:191`) unchanged, env var
   `APP_FILE_BROWSE_ROOTS`.
   → verify: `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/python -c "import sys;sys.path.insert(0,'apps/web-server');from server.routes._specpath import within_roots,browse_roots;print(browse_roots())"`
   prints a list including your home.
   Traps: `routes/projects.py` must not be imported at module top (late
   binding plus a possible cycle). The 403 detail must not echo the path. Ruff
   `select` is strict (`ruff.toml:52`), so run `ruff check` on the file.

6. `WS/routes/files.py:28,239`: import `browse_roots, within_roots` from
   `._specpath` (extend the existing line 28). Replace line 239 with
   `base = within_roots(base_path, browse_roots(), "browsable directories", expand_user=True)`.
   Keep the `exists()` / `is_dir()` checks that follow.
   → verify: `grep -n "within_roots" apps/web-server/server/routes/files.py`
   Traps: leave `/list` (316) and `/read` (371) alone (out of scope). The existing
   error messages echo `base_path`. Leave them as they are; they only run for
   paths already inside a root.

7. `.github/codeql/custom-queries/PathInjectionSanitized.ql:46-49,59-62`: add
   `"contained_path", "within_roots"` to the name list. Change the
   parameter-barrier clause to `f.getName() in ["trusted_project_root",
   "contained_path", "within_roots"]`. Add one line to the header comment:
   these confine a whole request path to server-owned roots.
   → verify: `grep -n "within_roots" .github/codeql/custom-queries/PathInjectionSanitized.ql` shows 2 hits.
   Traps: none.

8. `apps/web-server/tests/test_specpath.py` (extend) and
   `apps/web-server/tests/test_files_discover_roots.py` (new).
   specpath: `within_roots(tmp/"a", [tmp], "x")` returns the resolved path;
   `/etc` against `[tmp]` gives 403 with no path in `detail`;
   `tmp/"a/../../etc"` gives 403; `browse_roots()` with
   `monkeypatch.setenv("APP_FILE_BROWSE_ROOTS", str(tmp))` contains `tmp.resolve()`;
   with `server.routes.projects.load_projects` monkeypatched to
   `{"p": {"path": str(tmp/"proj")}}` it contains that path, and an entry without
   `path` is skipped. discover: mount `files.router` on `FastAPI()`; monkeypatch
   `load_projects` to `{}`, `HOME` to an empty tmp dir, and `APP_FILE_BROWSE_ROOTS`
   to `tmp/root` (which holds `proj/.git/`). `GET /discover?base_path=<tmp/root>`
   gives 200 with `proj` in `data`; `base_path=/etc` gives 403.
   → verify: `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest apps/web-server/tests/test_specpath.py apps/web-server/tests/test_files_discover_roots.py -q`, all pass.
   Mutation check: temporarily put back the old `Path(base_path).expanduser().resolve()`
   line, confirm the `/etc` test fails, then restore it.
   Traps: `Path.home()` reads `HOME`, so monkeypatch `HOME`, or the real home
   becomes a root and `/etc` tests still pass but by luck. `_is_app_internal_path`
   calls `get_settings()`. If it fails in the test, monkeypatch
   `files._is_app_internal_path` to `lambda p: False`.

9. `WS/routes/git.py:1230`: `if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):`.
   → verify: `grep -n 're.match(r"^\[a-zA-Z0-9_-\]+\$"' apps/web-server/server/routes/git.py` prints nothing.
   Traps: no test (the name is stripped first, so no input behaves differently).
   Say so in the PR.

10. Frontend (`apps/frontend-web/src`):
    - `lib/api-adapter.ts:423-426`: drop the `customPath` param and body field,
      and add AIFactory's two-line comment (server gives 400, AIFactory#1267/Factory#813).
    - `shared/types/ipc.ts:274-275`: drop `customPath?: string`.
    - `components/Terminal.tsx:230-243`, `components/task-detail/TaskFiles.tsx:226-234`,
      `components/task-detail/task-review/WorkspaceStatus.tsx:174-205`: drop the
      third argument and `settings.customIDEPath` from the `useCallback` deps.
    - `components/AddProjectModal.tsx:37`: `DEFAULT_PROJECTS_FOLDER = '~'`.
    → verify: `cd apps/frontend-web && npm run typecheck` passes (needs
    `node_modules`; if missing, run `npm ci` in the worktree first).
    `grep -rn "customIDEPath\|customTerminalPath" apps/frontend-web/src/components/{Terminal,task-detail}`
    prints nothing.
    Traps: leave `shared/types/settings.ts:280-282` and the settings/onboarding
    UI alone (out of scope). Check for a frontend test that asserts the `'/home'`
    default (`grep -rn "'/home'" apps/frontend-web/src`) and update it.

11. `.env.example` (in the `APP_*` web-server block, near line 336-339): add
    `# APP_FILE_BROWSE_ROOTS=        # os.pathsep list of extra dirs the project scan may browse (default: $HOME + registered projects)`.
    → verify: `grep -n APP_FILE_BROWSE_ROOTS .env.example`
    Traps: the repo has a secrets/config lint over `.env.example`. Keep the
    comment form the other lines use.

12. Full check, then commit, push and open the PR.
    `ruff check apps/web-server/server/routes/{worktree_tools,files,git,_specpath}.py apps/web-server/tests/test_{worktree_tools_custom_path,specpath,files_discover_roots}.py`
    and `ruff format --check` on the same files;
    `/mnt/code/Source-home/GitHub/TFactory/apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q` (CI's command, `ci.yml:88`).
    Commits: one per step group (D1+tests+ql, D2+tests+ql, D3, frontend+env),
    `fix(security): ... (Factory#813)`, each ending with the Co-Authored-By /
    Claude-Session trailer. Never `--no-verify` on code commits; hooks can
    take 10+ minutes, so use long timeouts. Push, then
    `gh pr create --base dev`. The body links intent/spec/plan, lists the four
    alerts, names the out-of-scope follow-ups and the D3 no-test reason, and ends
    with the Claude Code footer. No IPs, secrets or names.
    → verify: the PR's CodeQL check reports 1853, 1854, 1892 and 1893 fixed and no
    new alerts in touched files:
    `gh api "repos/olafkfreund/TFactory/code-scanning/alerts?ref=refs/pull/<n>/merge&state=open" --jq '.[]|select(.most_recent_instance.location.path|test("worktree_tools|files.py|git.py|_specpath"))|.number'`.
    Traps: if 1893 stays open, the taint is coming through `worktrees_base`.
    Record it on the PR as a separate finding, do not widen. If 1853/1854 stay
    open, check the twin compiled (CodeQL job log) before changing code.

## Tests

- `pytest apps/web-server/tests/test_worktree_tools_custom_path.py`: 4 pass.
- `pytest apps/web-server/tests/test_specpath.py apps/web-server/tests/test_files_discover_roots.py`: all pass.
- `pytest apps/web-server/tests/ -m "not slow" -q`: no new failures compared with `origin/dev`.
- `npm run typecheck` (frontend-web): passes.
- PR CodeQL: the four alerts are fixed and nothing new appears.

## Rollback

Revert the PR's merge commit on `dev`. Nothing else to clean up: no data,
schema, or migration changes. After a revert the RCE is open again, so re-land
as soon as you can, not "later".

# Custom CodeQL path-injection sanitizer — rollout

CodeQL's stock `py/path-injection` query does **not** model this repo's
sanitizers (`safe_component`, `safe_spec_dir`, `safe_join`, `os.path.basename`,
`get_next_spec_id`) as barriers, so verified-safe code keeps reporting alerts.
`custom-queries/PathInjectionSanitized.ql` re-emits the same `py/path-injection`
rule id with those sanitizers registered as `PathInjection::Sanitizer` barriers.
Validated locally against CodeQL 2.25.6: **306 stock flows → 21** (the residual
is by-design local-install paths).

## To activate (advanced setup)
1. Disable GitHub **default** code-scanning setup for the repo
   (Settings → Code security → CodeQL → switch to Advanced), or:
   `gh api -X PATCH repos/<owner>/<repo>/code-scanning/default-setup -f state=not-configured`
2. Rename `.github/workflows/codeql.yml.disabled` → `codeql.yml`.
3. Push; the advanced workflow runs the custom config and clears the
   sanitizer-covered alerts on `main`.

## Remaining residual (by-design)
`projects.py` register/scan, `files.py` absolute browsers, `git.py` cwd —
these intentionally accept an arbitrary local path (local-install trust
model). Decide per-endpoint: recognise the existing access guard as a barrier,
confine to an allowed root, or accept.

`terminal_worktree` base: DECIDED (issue #664, alerts #705-#709). The five
alerts were flows of the request-supplied project root — not `safe_component`
flows, which the pack already barriered. The accept decision now has a named
choke point, `routes/_specpath.trusted_project_root`, which the pack
recognises as a barrier, so those sinks clear on their merits.

---
status: approved
issue: 1624
spec: spec/2026-11-19-1624-tf-go-test-paths.md
coders:
  1: codex
reviewers:
  - codex
---

# Plan: Go test paths and unreadable-source handling

Carries every decision of spec/2026-11-19-1624-tf-go-test-paths.md (approved, owner advance approval 2026-11-19; intent approved likewise). Decision (owner delegation 2026-11-19): recommended option from the issue's own fix idea.

## Decisions (copied from the approved spec)


triager.py: `_read_test_source` becomes `_resolve_test_source(spec_dir, test_file) -> tuple[str, str]` returning (source, found_relative_path): try spec_dir/test_file, then spec_dir/basename(test_file); OSError and UnicodeDecodeError give source "". `_build_candidates` sets TriageCandidate.test_file to the found path on a fallback hit, so git_writer commits the file under its real (package-adjacent) name. A candidate is *unreadable* when `not source.strip()`. For an accept/flag verdict that is unreadable, `_build_candidates` replaces the verdict dict with a copy marked `verdict: "reject"` and a reason "test source not found or empty at <file>"; it therefore shows in the report's Rejected section, is never deduped, never committed, never counted as delivered, and gets no catalog lookup (rejects get none). triage_dedup.dedup_candidates also lets unreadable (blank) sources bypass both passes, so dedup alone can never collapse them. This one definition covers dedup, delivery and the commit filter; the `if c.source` filter in _run_git_side_effect becomes `c.source.strip()`. The catalog's derived create-path for a recovered file is pre-existing, framework-wide behaviour and out of scope.

Test matrix: New tests/test_triager_go_paths.py, each failing on current dev: (1) spec-root Go layout, 6 distinct sources, verdicts naming tests/<file>: 6 readable candidates, no collisions, test_file is the basename; (2) six blank and whitespace-only sources: dedup_candidates keeps all six with no collision; (3) _build_candidates turns blank accepts into rejects with the reason, and undecodable bytes do not raise; (4) mixed batch: only the readable file reaches GitWriteRequest.files and the unreadable ones are in rejected, not committed.

## Steps

1. apps/backend/agents/triager.py (_resolve_test_source, _build_candidates, _run_git_side_effect `c.source.strip()`) and apps/backend/agents/triage_dedup.py (blank sources bypass dedup) as in Decisions above. Step 1 is small tricky logic, so codex implements it; the lead reviews and commits.
2. Update tests/test_triager.py:~438, which expects committed_count == 2 with one source missing: it must now expect one committed and one rejected with the reason.
3. Add the Test matrix above to tests/test_triager_go_paths.py; confirm they fail on origin/dev (git stash is forbidden: check with the source edit reverted in a scratch copy, or by reading the failing assertion) and pass now. Run: python -m pytest tests/test_triager_go_paths.py tests/test_triager.py tests/test_triage_dedup.py tests/test_triager_delivery_honesty.py -q
   Traps: ratchet lint for apps/backend and apps/web-server; pre-commit may fail on the unrelated mcp-library collection error in test_mcp_remote_session_key.py, so commit with --no-verify and say why in the body.

## Tests

python -m pytest tests/test_triager_go_paths.py tests/test_triager.py tests/test_triage_dedup.py tests/test_triager_delivery_honesty.py -q; from the repo root, after committing: python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server.

## Rollback

Revert the commit; no data or config migration.

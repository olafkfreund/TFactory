---
status: draft
issue: 1624
intent: intent/2026-11-19-1624-tf-go-test-paths.md
---

# Spec: Go test paths and dedup of unreadable sources

## Design

triager.py `_read_test_source` becomes `_resolve_test_source(spec_dir, test_file) -> tuple[str, str]` returning (source, found_relative_path): try spec_dir/test_file, then spec_dir/basename(test_file); catch OSError and UnicodeDecodeError (-> source ""). `_build_candidates` uses the found path as TriageCandidate.test_file when a fallback hit, so git_writer commits the file under its real (package-adjacent) name rather than tests/<file>. triage_dedup.dedup_candidates: candidates whose source is empty/whitespace-only skip both passes and are kept as-is (never a collision); the existing `if c.source` filter in _run_git_side_effect keeps them out of the commit, and _build_candidates logs a warning naming them. Verdict dicts are untouched.

## Alternatives rejected

Dropping empty candidates in _build_candidates: they would vanish from the report. Changing the Go lane to write under tests/: Go tests must sit next to the package.

## Risks

A test with the same basename in tests/ and at root: tests/<file> wins (existing behaviour). Empty-source candidates now appear as kept but uncommitted; the report's delivery honesty (#1260) already reports uncommitted accepts.

## Verification

New tests in tests/test_triager_go_paths.py: (1) spec-root Go layout with 6 distinct sources, verdicts naming tests/<file>: 6 candidates, no collisions, test_file is the basename; (2) six empty sources: dedup_candidates reports no collision; (3) undecodable bytes give empty source without raising. They fail on current dev.

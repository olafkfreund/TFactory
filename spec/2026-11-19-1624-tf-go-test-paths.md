---
status: draft
issue: 1624
intent: intent/2026-11-19-1624-tf-go-test-paths.md
---

# Spec: Go test paths and dedup of unreadable sources

## Design

triager.py: \`_read_test_source\` becomes \`_resolve_test_source(spec_dir, test_file) -> tuple[str, str]\` returning (source, found_relative_path): try spec_dir/test_file, then spec_dir/basename(test_file); OSError and UnicodeDecodeError give source "". \`_build_candidates\` sets TriageCandidate.test_file to the found path on a fallback hit, so git_writer commits the file under its real (package-adjacent) name. A candidate is *unreadable* when \`not source.strip()\`. For an accept/flag verdict that is unreadable, \`_build_candidates\` replaces the verdict dict with a copy marked \`verdict: "reject"\` and a reason "test source not found or empty at <file>"; it therefore shows in the report's Rejected section, is never deduped, never committed, never counted as delivered, and gets no catalog lookup (rejects get none). triage_dedup.dedup_candidates also lets unreadable (blank) sources bypass both passes, so dedup alone can never collapse them. This one definition covers dedup, delivery and the commit filter; the \`if c.source\` filter in _run_git_side_effect becomes \`c.source.strip()\`. The catalog's derived create-path for a recovered file is pre-existing, framework-wide behaviour and out of scope.

## Alternatives rejected

Silently dropping them: they would vanish from the report. Keeping them as accepts: _delivery_error (triager.py:590) does not fail on a skipped write, so they would be reported as committed. Changing the Go lane to write under tests/: Go tests must sit next to the package.

## Risks

A test with the same basename in tests/ and at root: tests/<file> wins (existing behaviour). A whitespace-only generated test is now a reject with a reason instead of an accept that never lands.

## Verification

New tests/test_triager_go_paths.py, each failing on current dev: (1) spec-root Go layout, 6 distinct sources, verdicts naming tests/<file>: 6 readable candidates, no collisions, test_file is the basename; (2) six blank and whitespace-only sources: dedup_candidates keeps all six with no collision; (3) _build_candidates turns blank accepts into rejects with the reason, and undecodable bytes do not raise; (4) mixed batch: only the readable file reaches GitWriteRequest.files and the unreadable ones are in rejected, not committed.

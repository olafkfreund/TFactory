---
status: draft
issue: 1624
author: olafkfreund
---

# Intent: Go tests are found where the lane wrote them; unreadable sources never dedup

## Problem

On factory-lab-go PR #1 the Go lane wrote tests at the spec root but verdicts name tests/<file>. The triager read every source as empty, dedup collapsed 5 of 6 as byte_identical, and git_writer committed nothing (VAL-1 instead of VAL-2).

## Proposed outcome

A verdict whose file sits at the spec root is read from there and committed under its real name; a candidate with an unreadable or empty source is never treated as a duplicate of another.

## Affected users and systems

TFactory triager (apps/backend/agents/triager.py, triage_dedup.py); Go verifies.

## Constraints

Smallest correct change; tests that fail without the fix; no cluster changes; base dev; release lead ships 0.13.3.

## Open questions

None. Decision (owner delegation 2026-11-19): resolve test_file as spec_dir/<test_file>, else spec_dir/<basename>, and rewrite the candidate's test_file to where it was found; a source that is empty or undecodable (UnicodeDecodeError, not only OSError) counts as unreadable: it bypasses dedup and are not committed (git_writer already skips them).

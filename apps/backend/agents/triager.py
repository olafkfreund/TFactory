"""Triager agent — Task 8, issue #9 + Task 11, issue #27.

Final agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads ``findings/verdicts.json`` (written by the Evaluator), wraps
each entry as a TriageCandidate, filters out rejects, dedups +
ranks via the commit-2 primitives, renders triage_report.{md,json}
via commit-3's renderer, then invokes the commit-4 git_writer +
pr_comment helpers (both DRY-RUN by default for safety — production
opts in via env flags).

Per CLAUDE.md: "NO automatic pushes to GitHub - user controls when
to push". Real-run defaults are off; the operator flips them per
deployment.

Task 8 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Dedup + rank primitives
  ✓ commit 3 — Triage report rendering (golden-file snapshot)
  ✓ commit 4 — git_writer + pr_comment helpers (dry-run first)
  ✓ commit 5 — Real run_triager wires everything
  ✓ commit 6 — Integration test + close #9

  (Sub-task 8.4 — trim AIFactory's runners/github/ to a minimal
  pr_comment.py — is deferred to a follow-up commit; the web-server
  still consumes pieces of that tree and needs careful surgery.)

Task 11 commits (v0.2 catalog-aware Triager):

  ✓ commit 1 — Catalog read at Triager start + CandidateDecision dataclass
  ✓ commit 2 — lookup_by_ac integration + intent derivation
  ✓ commit 3 — UPDATE vs CREATE branching + framework path derivation
  ✓ commit 4 — SKIP for operator_locked + report rendering with intent
  ✓ commit 5 — Catalog mutation + 18+ test cases + close #27
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

# Single source of truth for the completion-envelope schema version (#360),
# derived from the vendored JSON schema's ``$id`` so the Python literal and the
# published contract can never silently drift. Imported at module scope (unlike
# the other lazy ``agents.*`` imports below) because the value is bound to a
# module-level constant used when building every completion envelope.
from agents.completion_schema import (  # noqa: E402 - agents pkg resolved via sys.path
    COMPLETION_SCHEMA_VERSION as _COMPLETION_SCHEMA_VERSION,
)

_triage_log = _logging.getLogger(__name__)


# ─── Task 11: per-candidate catalog decision ───────────────────────────


@dataclass(frozen=True)
class CandidateDecision:
    """Catalog-aware decision for one TriageCandidate (Task 11 / #27).

    Produced by ``_decide_catalog_intent`` after consulting the tests
    catalog via ``lookup_by_ac``.

    Attributes:
        intent: One of ``"create"``, ``"update"``, or ``"skip"``.
        update_target_file: Repo-relative path of the existing test file
            when ``intent == "update"``.  ``None`` otherwise.
        skip_reason: Human-readable reason for ``intent == "skip"``,
            e.g. ``"operator_locked"``.  ``None`` otherwise.
        derived_test_file: For ``intent == "create"``, the framework-
            conventional path for the new test file.  May be ``None``
            when the framework is unknown.
    """

    intent: Literal["create", "update", "skip"] = "create"
    update_target_file: str | None = None
    skip_reason: str | None = None
    derived_test_file: str | None = None


# ─── Task 11: catalog IO helpers ───────────────────────────────────────


def _load_catalog_from_spec(spec_dir: Path):
    """Load tests_catalog from ``spec_dir/context/tests_catalog.json``.

    Returns a ``TestsCatalog`` when the file exists and parses, or
    ``None`` when absent (v0.1-style run — every candidate is CREATE
    with no catalog mutation).

    Logs and returns ``None`` on parse failure rather than raising, to
    keep the Triager's happy path unaffected by a corrupt catalog file.
    """
    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        return None
    try:
        from tests_catalog import TestsCatalog

        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        return TestsCatalog.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        _triage_log.warning(
            "triager: could not parse tests_catalog.json — "
            "falling back to v0.1 (all CREATE): %s",
            exc,
        )
        return None


# ─── Task 11: lookup_by_ac + intent decision (commit 2) ────────────────


def _extract_candidate_ac(candidate) -> str:
    """Extract the AC string from a TriageCandidate for catalog lookup.

    The rationale field of the verdict dict carries the AC reference
    (e.g. ``"AC#1: login sets 24h expiry"``).  Returns ``""`` when
    the rationale is absent or empty — the caller treats that as a
    no-match, forcing CREATE intent.
    """
    rationale = candidate.verdict.get("rationale") or ""
    if not rationale:
        # Fallback: check the reasons list for an "AC#" prefix
        reasons = candidate.verdict.get("reasons") or []
        for r in reasons:
            if isinstance(r, str) and r.strip().startswith("AC"):
                return r.strip()
    return str(rationale).strip()


def _decide_catalog_intent(candidate, catalog) -> CandidateDecision:
    """Decide CREATE / UPDATE / SKIP for *candidate* against *catalog*.

    Args:
        candidate: A ``TriageCandidate`` (accepted or flagged).
        catalog: A ``TestsCatalog`` or ``None``.  When ``None`` every
            candidate is CREATE (v0.1 backward-compat path).

    Returns:
        A ``CandidateDecision`` with the appropriate intent fields set.

    Policy (from design doc, §"Update-vs-create policy"):
    - ``catalog is None``          → CREATE (no catalog at all)
    - ``len(matches) == 0``        → CREATE (no existing test)
    - ``matches[0].operator_locked`` → SKIP (operator pinned)
    - ``len(matches) == 1``        → UPDATE (clear single match)
    - ``len(matches) > 1``         → UPDATE on most-recent + warn
    """
    if catalog is None:
        return CandidateDecision(intent="create")

    from tests_catalog import lookup_by_ac

    candidate_ac = _extract_candidate_ac(candidate)
    matches = lookup_by_ac(catalog, candidate_ac) if candidate_ac else []

    if not matches:
        return CandidateDecision(intent="create")

    # Operator-locked check comes BEFORE the single/multi split so that
    # a locked entry always wins, even when it's the only match.
    if matches[0].operator_locked:
        return CandidateDecision(
            intent="skip",
            skip_reason="operator_locked",
        )

    if len(matches) > 1:
        _triage_log.warning(
            "triager: catalog ambiguity — %d entries match AC %r for "
            "candidate %r; picking most-recent entry as UPDATE target",
            len(matches),
            candidate_ac,
            candidate.test_id,
        )
        best = max(matches, key=lambda e: e.generated_at)
    else:
        best = matches[0]

    return CandidateDecision(
        intent="update",
        update_target_file=best.test_file,
    )


# ─── Task 11: framework-conventional path derivation (commit 3) ────────

# Fallback extension map when the framework registry cannot be loaded.
_FRAMEWORK_EXTENSION_FALLBACK: dict[str, str] = {
    "playwright": ".spec.ts",
    "jest": ".test.ts",
    "pytest": ".py",
}


def _derive_create_path(test_id: str, framework: str) -> str:
    """Derive the conventional test-file path for a new test.

    Consults the framework registry's ``test_path_conventions`` to pick
    the first pattern, replaces the glob wildcards with the test_id,
    and returns a repo-relative path string.

    Falls back to a sensible default when the framework is unknown or
    the registry is unavailable (e.g. in test environments without the
    ``frameworks/`` directory).

    Args:
        test_id: The test identifier (e.g. ``"ac1-login-flow"``).
        framework: Framework name string (e.g. ``"playwright"``).

    Returns:
        A repo-relative path string like ``"tests/e2e/ac1-login-flow.spec.ts"``.
    """
    try:
        from framework_registry.loader import get_descriptor

        desc = get_descriptor(framework)
        if desc.test_path_conventions:
            # Take the first convention pattern.
            pattern = desc.test_path_conventions[0]
            # Strip glob wildcards to get the directory prefix.
            # e.g. "tests/e2e/**/*.spec.ts" → dir="tests/e2e", ext=".spec.ts"
            # e.g. "tests/**/test_*.py"     → dir="tests",     ext=".py"
            # e.g. "**/*.test.ts"           → dir="",           ext=".test.ts"
            import posixpath

            parts = pattern.replace("\\", "/").split("/")
            dir_parts = [p for p in parts[:-1] if p and p != "**"]
            filename_pattern = parts[-1]
            # Extract extension from the filename glob
            if "." in filename_pattern:
                ext = filename_pattern[filename_pattern.rindex(".") :]
            else:
                ext = _FRAMEWORK_EXTENSION_FALLBACK.get(framework, ".py")
            dir_prefix = "/".join(dir_parts) if dir_parts else "tests"
            return posixpath.join(dir_prefix, f"{test_id}{ext}")
    except Exception as exc:  # noqa: BLE001
        # Registry unavailable (expected in test envs without frameworks/) or
        # a malformed convention pattern — fall back below, but leave a trail.
        _triage_log.debug(
            "could not derive create-path from registry for %r/%r: %s — "
            "using extension fallback",
            test_id,
            framework,
            exc,
        )

    # Fallback: derive from known extensions
    ext = _FRAMEWORK_EXTENSION_FALLBACK.get(framework, ".py")
    return f"tests/{test_id}{ext}"


# ─── Task 11: catalog mutation (commit 5) ──────────────────────────────


def _mutate_catalog(
    catalog,
    candidates,
    decisions: dict[str, CandidateDecision],
    generated_by_task: str,
    now_ts: str,
) -> TestsCatalog | None:
    """Apply UPDATE / CREATE decisions to *catalog* and return a new catalog.

    Per the design doc policy:
    - UPDATE: bump generation_version; refresh generated_at + last_verdict
    - CREATE: append a new CatalogEntry
    - SKIP (operator_locked): leave entry untouched
    - REJECT candidates: not in decisions dict → no catalog mutation

    Args:
        catalog: The current ``TestsCatalog`` (may be ``None`` for v0.1
            runs — returns ``None`` in that case so the caller skips
            save_catalog).
        candidates: All TriageCandidates (accept + flag only — rejects
            are already excluded from decisions).
        decisions: Mapping test_id → CandidateDecision from the intent
            derivation step.
        generated_by_task: Spec-ID to record in new catalog entries.
        now_ts: ISO-8601 UTC timestamp string to use for generated_at.

    Returns:
        An updated ``TestsCatalog``, or ``None`` when *catalog* is
        ``None`` (v0.1 flow — no catalog file at all).
    """
    if catalog is None:
        return None

    from tests_catalog import CatalogEntry, TestsCatalog

    # Build a mutable list so we can update in place.
    entries: list = list(catalog.tests)

    # Index existing entries by test_file for O(1) UPDATE lookup.
    file_index: dict[str, int] = {e.test_file: i for i, e in enumerate(entries)}

    for c in candidates:
        decision = decisions.get(c.test_id)
        if decision is None:
            continue  # reject or unprocessed

        if decision.intent == "skip":
            # operator_locked — leave the catalog entry untouched
            continue

        verdict_label = c.verdict_label

        if decision.intent == "update" and decision.update_target_file:
            idx = file_index.get(decision.update_target_file)
            if idx is not None:
                old = entries[idx]
                # Frozen dataclass — replace with new instance
                entries[idx] = CatalogEntry(
                    test_id=old.test_id,
                    test_file=old.test_file,
                    framework=old.framework,
                    lane=old.lane,
                    language=old.language,
                    covers_acs=old.covers_acs,
                    generated_at=now_ts,
                    generated_by_task=old.generated_by_task,
                    last_verdict=verdict_label,
                    browsers_tested=old.browsers_tested,
                    target_ref=old.target_ref,
                    operator_locked=old.operator_locked,
                    generation_version=old.generation_version + 1,
                )

        elif decision.intent == "create":
            test_file = decision.derived_test_file or c.test_file
            # Avoid duplicate entries if the same test appears twice
            if test_file not in file_index:
                verdict_dict = c.verdict
                framework = verdict_dict.get("framework") or "pytest"
                language = verdict_dict.get("language") or "python"
                lane_str = verdict_dict.get("lane") or "unit"
                new_entry = CatalogEntry(
                    test_id=c.test_id,
                    test_file=test_file,
                    framework=framework,
                    lane=lane_str,
                    language=language,
                    covers_acs=tuple(verdict_dict.get("covers_acs") or []),
                    generated_at=now_ts,
                    generated_by_task=generated_by_task,
                    last_verdict=verdict_label,
                    generation_version=1,
                )
                entries.append(new_entry)
                file_index[test_file] = len(entries) - 1

    return TestsCatalog(
        version=catalog.version,
        updated_at=now_ts,
        tests=tuple(entries),
    )


# ─── Workspace helpers ─────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# Terminal statuses the Triager can land on. A completion callback fires once
# the task reaches any of these (see _notify_completion).
_TERMINAL_STATUSES = frozenset({"triaged", "triaged_empty", "triager_failed"})


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Best-effort push-based progress event (#95); no-op unless opted in.
    from agents.stage_events import emit_stage_event

    emit_stage_event(spec_dir, status, stage="triager")
    # Fire the completion callback exactly once, when the task goes terminal.
    if fields.get("status") in _TERMINAL_STATUSES:
        _notify_completion(spec_dir, status)
        # Best-effort TFactory→AIFactory correction hand-back (#185 / epic #182).
        # Prepares findings/handback_request.{md,json} when the run has failures;
        # sends to AIFactory only with TFACTORY_HANDBACK_SEND=1. Never raises.
        from agents.handback.trigger import maybe_handback

        maybe_handback(spec_dir)
        # Best-effort per-component test-quality fact to Backstage (#240).
        # No-op unless TFACTORY_BACKSTAGE_TECHINSIGHTS_URL is set; never raises.
        try:
            from agents.backstage_integration import maybe_emit_backstage

            maybe_emit_backstage(spec_dir, status)
        except Exception:  # noqa: BLE001 — emitting must never break the run
            pass
        # Best-effort test-result docs via the vendored docs-emit core (#341).
        # No-op unless TFACTORY_DOCS_EMIT is set; publishes under the plan's
        # correlation_key so the run's results resolve next to the plan it
        # verifies (verify → docs). Never raises.
        try:
            from agents.docs_emit_trigger import maybe_emit_docs

            maybe_emit_docs(spec_dir, status)
        except Exception:  # noqa: BLE001 — emitting must never break the run
            pass


# ─── Mode resolution: dry-run vs real ─────────────────────────────────


def _truthy(env_val: str | None) -> bool:
    if env_val is None:
        return False
    return env_val.strip().lower() in ("1", "true", "yes", "on")


def _git_writer_dry_run() -> bool:
    """Default ON (dry). Operator sets TFACTORY_TRIAGER_GIT_WRITE=1 to
    actually commit to the AIFactory branch."""
    return not _truthy(os.environ.get("TFACTORY_TRIAGER_GIT_WRITE"))


def _pr_comment_dry_run() -> bool:
    """Default ON (dry). Operator sets TFACTORY_TRIAGER_PR_COMMENT=1 to
    actually post via gh pr comment."""
    return not _truthy(os.environ.get("TFACTORY_TRIAGER_PR_COMMENT"))


def _pr_status_dry_run() -> bool:
    """Default ON (dry). Operator sets TFACTORY_PR_STATUS=1 to actually publish
    the quality-gate commit status (WS1)."""
    return not _truthy(os.environ.get("TFACTORY_PR_STATUS"))


def _harvest_enabled() -> bool:
    """Default ON. Promote high-confidence accepts into the reusable template
    library. Writing template files into ``<project>/.tfactory/templates/`` is
    low-risk, so this is on by default; set TFACTORY_TRIAGER_HARVEST=0 to skip."""
    env_val = os.environ.get("TFACTORY_TRIAGER_HARVEST")
    return env_val is None or _truthy(env_val)


def _harvest_global() -> bool:
    """Also write harvested templates to the cross-project global library at
    ``~/.tfactory/templates/``. Opt-in via TFACTORY_TRIAGER_HARVEST_GLOBAL=1."""
    return _truthy(os.environ.get("TFACTORY_TRIAGER_HARVEST_GLOBAL"))


# ─── Completion callback (#85) ──────────────────────────────────────────
# When the Triager reaches a terminal status, optionally notify a watcher so
# the /tfactory-watch round-trip needs no polling. Both channels are OFF by
# default (consistent with the "no automatic side-effects" policy) and are
# strictly best-effort — a missing/failing target must never affect the run.


def _completion_webhook_url() -> str | None:
    """Webhook URL POSTed on completion. Opt-in via TFACTORY_COMPLETION_WEBHOOK."""
    url = (os.environ.get("TFACTORY_COMPLETION_WEBHOOK") or "").strip()
    return url or None


def _completion_sentinel_enabled() -> bool:
    """Write findings/COMPLETED.json on completion. Opt-in via
    TFACTORY_COMPLETION_SENTINEL=1 — a same-host watcher can stat it."""
    return _truthy(os.environ.get("TFACTORY_COMPLETION_SENTINEL"))


# ─── Normalized completion-event envelope (#198, Factory PARR-spine) ─────
# v1 of the cross-service completion envelope. AIFactory / PFactory / TFactory
# all emit this shape so a watcher (CFactory) consumes one schema. The spine
# correlation key is the GitHub issue number threaded end-to-end. See
# docs/completion-event-envelope.md for the contract.
# Envelope grew additively in #282 (CloudEvents-core + id + W3C trace context).
# Mirrors AIFactory's #466 upgrade field-for-field so the two producers emit a
# parity envelope the CFactory collector can treat uniformly. The additive
# fields are built in agents/completion_envelope.py (testable in isolation).
# ``_COMPLETION_SCHEMA_VERSION`` is the shared single-source-of-truth constant
# imported at the top of this module from agents/completion_schema.py (#360).


def _outcome_for_status(status_value: str | None) -> str:
    """Map a terminal TFactory status to a normalized coarse outcome."""
    if status_value == "triaged":
        return "success"
    if status_value == "triaged_empty":
        return "empty"
    return "failure"


def _correlation_issue_number(status: dict, source: dict) -> int | None:
    """The GitHub issue number threading the PARR spine end-to-end.

    Read from status.json or source.json (populated by the PFactory pickup
    contract, #193). Returns None until a run carries one.
    """
    for src in (status, source):
        raw = src.get("issue_number")
        if raw is None:
            raw = src.get("correlation_id")
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _contract_correlation_key(spec_dir: Path) -> str | None:
    """The explicit ``correlation_key`` from an RFC-0002 contract, if present (#249).

    This is the cross-factory shared key PFactory minted; preferring it keeps
    the completion event and the hand-back reconciled on one identifier.
    """
    try:
        from agents.task_contract import read_task_contract

        contract = read_task_contract(spec_dir) or {}
        key = contract.get("correlation_key")
        return key.strip() if isinstance(key, str) and key.strip() else None
    except Exception:  # noqa: BLE001 — never break completion on a contract read
        return None


def _correlation_key(spec_dir: Path, status: dict, source: dict) -> str:
    """The RFC-0001 shared correlation key. Precedence (#249):
    RFC-0002 contract ``correlation_key`` → GitHub issue number → synthetic
    ``tf-<spec_id>`` fallback so it is never null (RFC-0001 §2)."""
    contract_key = _contract_correlation_key(spec_dir)
    if contract_key:
        return contract_key
    issue = _correlation_issue_number(status, source)
    if issue is not None:
        return str(issue)
    spec_id = status.get("spec_id") or source.get("spec_id") or spec_dir.name
    return f"tf-{spec_id}"


def _completion_result_summary(status: dict) -> dict:
    """Service-specific result counts for the envelope (absent keys omitted)."""
    keys = (
        "committed_count",
        "flagged_count",
        "rejected_count",
        "verdicts_count",
        "dedup_collision_count",
    )
    return {k: status[k] for k in keys if k in status}


def _build_completion_envelope(spec_dir: Path, status: dict) -> dict:
    """Build the v1 normalized completion-event envelope (#198).

    The flat #85 fields (``task_id``, ``project_id``, ``status``, ``phase``,
    ``updated_at``) are retained for backward-compat; the normalized header
    (``schema_version``, ``event``, ``service``, ``correlation_id``,
    ``outcome``) plus repo/branch context + a result summary sit on top.
    """
    from agents.completion_envelope import cloudevents_fields
    from usage import usage_block_from_status

    source = _load_source_meta(spec_dir)
    status_value = status.get("status")
    issue_number = _correlation_issue_number(status, source)
    when = status.get("updated_at") or _now_iso()

    # RFC-0001a evidence gate: a verify may only report a success OUTCOME if it
    # produced real verdicts. A "triaged" with zero verdicts evaluated nothing —
    # downgrade the normalized `outcome` to failure with a no_evidence reason so
    # no consumer renders it green. We do NOT rewrite TFactory's internal
    # `status` (its state machine + handback read it) and we deliberately do NOT
    # treat all-flagged as a failure — `flag` means "needs human attention" by
    # design and drives the handback loop, which is a valid non-failure outcome.
    _verdicts = int(status.get("verdicts_count") or 0)
    evidence = {
        "proof_kind": "tests",
        "verdicts": _verdicts,
        "accepted": int(status.get("committed_count") or 0),
        "flagged": int(status.get("flagged_count") or 0),
        "rejected": int(status.get("rejected_count") or 0),
    }
    outcome = _outcome_for_status(status_value)
    halt_reason: str | None = None
    # Actionable evidence = any real verdict produced (evaluated, accepted, or
    # flagged). A "triaged" success with NONE of these evaluated nothing.
    _actionable = _verdicts > 0 or evidence["accepted"] > 0 or evidence["flagged"] > 0
    if status_value == "triaged" and not _actionable:
        outcome = "failure"
        halt_reason = "no_evidence: verify produced no verdicts"

    envelope = {
        # RFC-0001 core: the six required fields (Factory#4). `correlation_key`
        # is the shared key (issue#, synthetic `tf-<spec_id>` fallback) so the
        # CFactory collector can thread this event into a WorkItem.
        "correlation_key": _correlation_key(spec_dir, status, source),
        "service": "tfactory",
        "task_id": status.get("task_id") or spec_dir.name,
        "status": status_value,
        "phase": status.get("phase") or "test",
        "updated_at": when,
        # RFC-0001 §4 optional chain block (upstream/downstream links).
        "correlation": {
            "issue_number": issue_number,
            "spec_id": status.get("spec_id") or source.get("spec_id"),
            "branch": source.get("branch"),
            "pr_number": source.get("pr_number") or None,
        },
        # Additive TFactory detail (RFC §7 — extra fields are allowed) + the
        # #85/#198 flat fields retained for backward-compat.
        "schema_version": _COMPLETION_SCHEMA_VERSION,
        "event": "completion",
        "correlation_id": issue_number,
        "project_id": status.get("project_id"),
        "spec_id": status.get("spec_id") or source.get("spec_id"),
        "outcome": outcome,
        "repo": source.get("repo_slug") or source.get("repo"),
        "branch": source.get("branch"),
        "pr_number": source.get("pr_number") or None,
        "result": _completion_result_summary(status),
        # RFC-0001 v1.1 §3.1 additive usage block (#224). Zeros when the run did
        # no LLM work; summed across sessions + handback retries via status.json.
        "usage": usage_block_from_status(spec_dir),
        "emitted_at": _now_iso(),
        # Additive #282 (parity with AIFactory #466). Per-event idempotency id +
        # CloudEvents-core + W3C trace context, all riding alongside the legacy
        # fields above — nothing removed until the cross-repo cutover (#284).
        # `time` mirrors the envelope timestamp; the #281 outbox keys its
        # Idempotency-Key on `id`, so the id is stable across relay re-delivery.
        **cloudevents_fields(project_id=status.get("project_id"), time_iso=when),
    }
    # RFC-0001a additive evidence block + the no-evidence reason (when gated).
    envelope["evidence"] = evidence
    if halt_reason is not None:
        envelope["halt_reason"] = halt_reason
    # RFC-0007 (#87): when the contract declared access that couldn't be curated/
    # reached, surface an honest VAL-3 not_run annotation so no consumer renders
    # the credentialed lane as covered. Best-effort — never breaks the envelope.
    try:
        from agents.access_scope import completion_access_annotation
        from agents.task_contract import read_tfactory_profile

        _prof = read_tfactory_profile(spec_dir)
        _acc = completion_access_annotation(_prof.access) if _prof else None
        if _acc:
            envelope["access"] = _acc
    except Exception:  # noqa: BLE001 - the access annotation must never break emit
        pass
    # RFC-0006 (#74): attribute the lanes that actually ran (verdicts.json) to
    # VAL levels and attach the gate-normalized verification block — honest
    # achieved_level + claim, with VAL-3 surfaced as a gap (no disposable target,
    # #75). CFactory renders it (#76) so a VAL-2 result never looks like "done".
    # Best-effort; also persisted to findings/verification.json.
    try:
        from agents.val_block import DEFAULT_TARGET_LEVEL, read_verification_block

        # RFC-0011: a higher autonomy_tier raises the VAL *floor* (target level).
        # Absent/unknown tier => keep the default target (back-compat). The gate
        # still recomputes achieved_level from what truly ran, so raising the
        # floor can never overclaim — it only surfaces a larger honest gap.
        _target = DEFAULT_TARGET_LEVEL
        try:
            from agents.task_contract import read_task_contract
            from agents.tier_floor import tier_from_contract, val_floor_for

            _floor = val_floor_for(tier_from_contract(read_task_contract(spec_dir)))
            if _floor is not None:
                _target = _floor
        except Exception:  # noqa: BLE001 - tier read must never break emit
            pass

        _block = read_verification_block(spec_dir, target_level=_target)
        envelope["verification"] = _block
        _fdir = spec_dir / "findings"
        _fdir.mkdir(parents=True, exist_ok=True)
        (_fdir / "verification.json").write_text(json.dumps(_block, indent=2))
    except Exception:  # noqa: BLE001 - verification block must never break emit
        pass
    return envelope


def _notify_completion(spec_dir: Path, status: dict) -> None:
    """Best-effort terminal callback. Writes a local sentinel (opt-in) and
    POSTs an env-gated webhook (opt-in), both carrying the v1 normalized
    completion-event envelope (#198). Every failure is swallowed so the
    pipeline can never break on notification."""
    payload = _build_completion_envelope(spec_dir, status)

    if _completion_sentinel_enabled():
        try:
            findings_dir = spec_dir / "findings"
            findings_dir.mkdir(parents=True, exist_ok=True)
            (findings_dir / "COMPLETED.json").write_text(json.dumps(payload, indent=2))
        except OSError:
            pass

    url = _completion_webhook_url()
    if not url:
        return

    # At-least-once path (#281): when TFACTORY_COMPLETION_OUTBOX is set, persist
    # the event to the durable outbox *before* attempting delivery, then drain
    # it. A crash between the terminal write and a successful POST no longer
    # loses the event — the relay replays it on the next pass. Default-off keeps
    # the legacy fire-and-forget behaviour unchanged (non-breaking, epic #284).
    try:
        from agents.completion_outbox import enqueue, outbox_enabled, relay_once

        if outbox_enabled():
            enqueue(payload)
            relay_once()  # best-effort immediate delivery; undelivered persist
            return
    except Exception:
        # Outbox must never break the pipeline; fall through to legacy POST.
        pass

    try:
        import urllib.request

        timeout = float(os.environ.get("TFACTORY_COMPLETION_WEBHOOK_TIMEOUT", "5"))
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout).close()  # noqa: S310
    except Exception:
        # Webhook is best-effort; never surface failures into the pipeline.
        pass


# ─── The agent itself ───────────────────────────────────────────────────


def _load_verdicts_or_fail(spec_dir: Path) -> list | None:
    """Read findings/verdicts.json tolerantly (#96 envelope extractor).

    Returns the verdicts list (possibly empty), or None after writing a
    ``triager_failed`` status when the file is missing or unparseable.
    """
    verdicts_path = spec_dir / "findings" / "verdicts.json"
    if not verdicts_path.exists():
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase="triager_no_verdicts",
            triager_error="findings/verdicts.json not found",
        )
        return None
    from agents.output_envelope import OutputEnvelopeError, extract_json

    try:
        verdicts_doc, _ = extract_json(verdicts_path.read_text())
    except OutputEnvelopeError as exc:
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase="triager_verdicts_unparseable",
            triager_error=f"verdicts.json invalid: {exc}",
        )
        return None
    return verdicts_doc.get("verdicts") or []


def _read_test_source(spec_dir: Path, test_file: str) -> str:
    """Read a generated test file's source, or '' if absent/unreadable."""
    test_path = spec_dir / test_file
    if not test_path.exists():
        return ""
    try:
        return test_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_candidates(spec_dir: Path, verdicts: list) -> list:
    """Wrap each well-formed verdict as a TriageCandidate (with its source)."""
    from agents.triage_dedup import TriageCandidate

    candidates = []
    for v in verdicts:
        tid = v.get("test_id")
        test_file = v.get("test_file")
        if not tid or not test_file:
            # Skip malformed entries — the Evaluator's validator should have
            # caught them, but be defensive.
            continue
        candidates.append(
            TriageCandidate(
                test_id=tid,
                test_file=test_file,
                verdict=v,
                source=_read_test_source(spec_dir, test_file),
            )
        )
    return candidates


def _resolve_catalog_decisions(candidates: list, catalog) -> dict:
    """Decide the catalog intent (create/update/skip) for each accept/flag
    candidate; rejects get no lookup. (Task 11 / #27 commits 2-3.)

    CREATE intents are enriched with a framework-conventional path.
    """
    decisions: dict[str, CandidateDecision] = {}
    for c in candidates:
        if c.verdict_label not in ("accept", "flag"):
            continue
        base_decision = _decide_catalog_intent(c, catalog)
        if base_decision.intent == "create":
            framework = c.verdict.get("framework") or "pytest"
            decisions[c.test_id] = CandidateDecision(
                intent="create",
                derived_test_file=_derive_create_path(c.test_id, framework),
            )
        else:
            decisions[c.test_id] = base_decision
    return decisions


def _bucket_candidates(candidates: list, decisions: dict) -> tuple[list, list, list]:
    """Split candidates into (keepers, skipped, rejects) in a single pass.

    SKIP candidates (operator_locked) are excluded from dedup/rank so they are
    not committed or flagged — they only appear in the report's skip section.
    """
    keepers, skipped, rejects = [], [], []
    for c in candidates:
        if c.verdict_label == "reject":
            rejects.append(c)
        elif c.verdict_label in ("accept", "flag"):
            intent = decisions.get(c.test_id, CandidateDecision()).intent
            (skipped if intent == "skip" else keepers).append(c)
    return keepers, skipped, rejects


def _dedup_and_rank(keepers: list) -> tuple:
    """Dedup the keepers and rank survivors.

    Returns ``(dedup_result, committed, flagged)`` where committed/flagged are
    the ranked survivors re-bucketed by verdict label.
    """
    from agents.triage_dedup import dedup_candidates, rank_candidates

    dedup_result = dedup_candidates(keepers)
    ranked = rank_candidates(dedup_result.kept)
    committed = tuple(c for c in ranked if c.verdict_label == "accept")
    flagged = tuple(c for c in ranked if c.verdict_label == "flag")
    return dedup_result, committed, flagged


def _render_and_write_report(
    spec_dir,
    mode,
    committed,
    flagged,
    rejects,
    skipped,
    dedup_result,
    keepers,
    decisions,
) -> tuple:
    """Build the report and write triage_report.{json,md}; return (report, report_md)."""
    from agents.triage_report import build_report, render_json, render_markdown

    # Resolve the SUT's Backstage entity ref for catalog linkage (#241).
    # Best-effort — a resolution miss just omits the line.
    component_ref = None
    try:
        from agents.backstage_integration import _component_ref

        component_ref = _component_ref(_load_source_meta(spec_dir))
    except Exception:  # noqa: BLE001
        component_ref = None

    report = build_report(
        mode=mode,
        generated_at=_now_iso(),
        committed=committed,
        flagged=flagged,
        rejected=tuple(rejects),
        skipped=tuple(skipped),
        collisions=dedup_result.collisions,
        dedup_input_count=len(keepers),
        decisions=decisions,
        spec_dir=spec_dir,
        component_ref=component_ref,
    )
    findings_dir = spec_dir / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "triage_report.json").write_text(render_json(report))
    report_md = render_markdown(report)
    # RFC-0006 (#76): lead the PR comment with the honest assurance-level claim so
    # a reviewer can never read a VAL-2 result as fully "done". Best-effort.
    try:
        from agents.val_block import read_verification_block

        _claim = read_verification_block(spec_dir).get("claim")
        if _claim:
            report_md = f"**Verification:** {_claim}\n\n{report_md}"
    except Exception:  # noqa: BLE001 - the claim header must never break the report
        pass
    (findings_dir / "triage_report.md").write_text(report_md)
    return report, report_md


def _write_ac_fidelity(spec_dir, committed, flagged, rejects) -> dict:
    """Build + write the per-AC fidelity ledger (findings/ac_fidelity.{json,md}).

    Maps the triager's own accept/flag/reject decisions back to the plan's
    acceptance criteria so the run reports which ACs are actually VERIFIED (>=1
    accepted test) vs flagged-only vs unverified — the honest headline, never a
    blanket "done". Returns the summary for status.json; never breaks triage.
    """
    try:
        from agents.ac_fidelity import (
            attach_screenshots,
            build_ac_ledger,
        )
        from agents.ac_fidelity import (
            render_markdown as _ac_md,
        )

        plan_path = spec_dir / "test_plan.json"
        if not plan_path.is_file():
            return {}
        plan = json.loads(plan_path.read_text())
        verdicts = (
            [
                {"test_id": c.test_id, "test_file": c.test_file, "verdict": "accept"}
                for c in committed
            ]
            + [
                {"test_id": c.test_id, "test_file": c.test_file, "verdict": "flag"}
                for c in flagged
            ]
            + [
                {"test_id": c.test_id, "test_file": c.test_file, "verdict": "reject"}
                for c in rejects
            ]
        )
        ledger = attach_screenshots(
            build_ac_ledger(plan, verdicts), spec_dir / "findings"
        )
        fd = spec_dir / "findings"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "ac_fidelity.json").write_text(json.dumps(ledger, indent=2))
        (fd / "ac_fidelity.md").write_text(_ac_md(ledger))
        return ledger.get("summary", {})
    except Exception as exc:  # noqa: BLE001 - evidence is best-effort, never fatal
        _triage_log.warning("ac_fidelity write failed (non-blocking): %s", exc)
        return {}


def _run_git_side_effect(project_dir, committed, flagged, source_meta) -> dict:
    """Commit accepted+flagged test files to the feature branch (dry-run by default).

    Returns a summary dict for status.json. Skips cleanly when there's nothing
    to commit, no branch in source.json, or no readable sources.
    """
    if not (committed or flagged):
        return {"skipped": True, "reason": "no side-effect path"}

    from tools.git_writer import GitWriteRequest, write_tests_to_branch

    branch = source_meta.get("branch") or ""
    files_to_commit = tuple(
        (c.test_file, c.source) for c in (*committed, *flagged) if c.source
    )
    if not (branch and files_to_commit):
        return {
            "skipped": True,
            "reason": (
                "no branch in source.json" if not branch else "no readable test sources"
            ),
        }
    request = GitWriteRequest(
        repo_dir=project_dir,
        branch=branch,
        files=files_to_commit,
        commit_msg=(
            f"tfactory: add {len(committed)} accepted + {len(flagged)} flagged tests"
        ),
    )
    git_write_result = write_tests_to_branch(request, dry_run=_git_writer_dry_run())
    return {
        "skipped": False,
        "dry_run": git_write_result.dry_run,
        "ok": git_write_result.ok,
        "committed_paths": list(git_write_result.committed_paths),
        "commit_sha": git_write_result.commit_sha,
        "error": git_write_result.error,
        "argv_log": [list(a) for a in git_write_result.argv_log],
    }


def _run_pr_side_effect(project_dir, findings_dir, source_meta, report_md) -> dict:
    """Post the report to the PR (dry-run by default), or write the body to disk
    when there's no PR number. Returns a summary dict for status.json."""
    pr_number = int(source_meta.get("pr_number") or 0)
    if pr_number > 0 and report_md:
        from tools.pr_comment import PRCommentRequest, post_pr_comment

        request = PRCommentRequest(
            repo_dir=project_dir,
            pr_number=pr_number,
            body=report_md,
            repo_slug=source_meta.get("repo_slug") or None,
        )
        pr_comment_result = post_pr_comment(request, dry_run=_pr_comment_dry_run())
        return {
            "skipped": False,
            "dry_run": pr_comment_result.dry_run,
            "ok": pr_comment_result.ok,
            "argv": list(pr_comment_result.argv),
            "body_bytes": pr_comment_result.body_bytes,
            "comment_url": pr_comment_result.comment_url,
            "error": pr_comment_result.error,
        }
    # No PR number — write the comment body to disk for manual posting.
    (findings_dir / "pr_comment_body.md").write_text(report_md)
    return {
        "skipped": True,
        "reason": "no PR number in source.json",
        "body_written_to": str(findings_dir / "pr_comment_body.md"),
    }


def _load_quality_gate_policy(spec_dir: Path):
    """Build the WS1 GatePolicy from the snapshotted ``.tfactory.yml``.

    Reads the ``quality_gate`` block of ``context/tfactory_yml.json`` (written by
    the snapshotter). Returns a default (disabled) policy when the file or block
    is absent or unreadable — the gate must never be the thing that breaks a run.
    """
    from agents.quality_gate import GatePolicy

    cfg_path = spec_dir / "context" / "tfactory_yml.json"
    if not cfg_path.exists():
        return GatePolicy()
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return GatePolicy()
    block = cfg.get("quality_gate") if isinstance(cfg, dict) else None
    return GatePolicy.from_mapping(block)


def _run_pr_status_side_effect(
    project_dir, findings_dir, source_meta, spec_dir
) -> dict:
    """Publish the WS1 quality-gate commit status (dry-run by default).

    No-op unless the ``.tfactory.yml`` ``quality_gate`` block is enabled. Reads
    ``findings/verdicts.json``, evaluates the gate, and posts a GitHub commit
    status to the head SHA. Best-effort: any failure is captured into the
    returned summary, never raised.
    """
    from agents.quality_gate import evaluate_gate

    policy = _load_quality_gate_policy(spec_dir)
    if not policy.enabled:
        return {"skipped": True, "reason": "quality_gate not enabled"}

    sha = source_meta.get("sha")
    repo_slug = source_meta.get("repo_slug") or source_meta.get("repo")
    if not sha or not repo_slug:
        return {"skipped": True, "reason": "no sha/repo in source.json"}

    verdicts_path = findings_dir / "verdicts.json"
    try:
        gate = evaluate_gate(verdicts_path, policy)
    except ValueError as exc:
        return {"skipped": True, "reason": f"gate not evaluated: {exc}"}

    target_url = ""
    pr_number = int(source_meta.get("pr_number") or 0)
    if pr_number > 0:
        target_url = f"https://github.com/{repo_slug}/pull/{pr_number}"

    from tools.pr_status import PRStatusRequest, post_pr_status

    request = PRStatusRequest(
        repo_dir=project_dir,
        repo_slug=repo_slug,
        sha=sha,
        state=gate.state,
        context=policy.context,
        description=gate.summary,
        target_url=target_url,
    )
    result = post_pr_status(request, dry_run=_pr_status_dry_run())
    return {
        "skipped": False,
        "passed": gate.passed,
        "state": gate.state,
        "summary": gate.summary,
        "reasons": list(gate.reasons),
        "counts": gate.counts,
        "dry_run": result.dry_run,
        "ok": result.ok,
        "argv": list(result.argv),
        "error": result.error,
    }


def _persist_catalog_mutation(
    spec_dir, catalog, keepers, skipped, decisions, generated_by_task, generated_at
) -> None:
    """Mutate + persist the workspace tests_catalog.json (Task 11 / #27 commit 5).

    Writes to spec_dir/context/tests_catalog.json — the Triager's scratch copy;
    the authoritative AIFactory-repo catalog is updated by git_writer. Non-fatal:
    a write failure degrades to a warning rather than failing the whole Triager.
    """
    all_decided = list(keepers) + list(skipped)
    updated_catalog = _mutate_catalog(
        catalog=catalog,
        candidates=all_decided,
        decisions=decisions,
        generated_by_task=generated_by_task,
        now_ts=generated_at,
    )
    if updated_catalog is None:
        return
    catalog_out = spec_dir / "context" / "tests_catalog.json"
    catalog_out.parent.mkdir(parents=True, exist_ok=True)
    # The snapshotter pins context/ files read-only (0o444), so make the catalog
    # writable before overwriting.
    try:
        if catalog_out.exists():
            catalog_out.chmod(0o644)
        catalog_out.write_text(
            json.dumps(
                updated_catalog.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        )
    except OSError as exc:
        _triage_log.warning(
            "triager: could not update workspace tests_catalog.json (non-fatal): %s",
            exc,
        )


def _maybe_harvest(spec_dir, project_dir, keepers) -> None:
    """Harvest high-confidence accepts into the reusable template library.

    Non-fatal: a harvest failure must never fail the Triager.
    """
    if not _harvest_enabled():
        return
    try:
        from agents.template_harvest import harvest_accepted_tests

        harvested = harvest_accepted_tests(
            spec_dir,
            project_dir,
            keepers,
            also_global=_harvest_global(),
        )
        if harvested:
            _triage_log.info(
                "triager: harvested %d accepted test(s) into the reusable "
                "template library",
                len(harvested),
            )
    except Exception as exc:  # noqa: BLE001 — non-fatal side-effect
        _triage_log.warning("triager: template harvest failed (non-fatal): %s", exc)


async def run_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Triager agent.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root_path (read; used as the
            git_writer's repo_dir when committing).
        mode: 'initial' on first run; 'rerun' for retriggers. Surfaced
            in the report.
        verbose: forwarded for log-level routing in future commits.

    Returns:
        True on clean completion (including dry-run + empty cases);
        False on hard failure.

    Status transitions:
      evaluated   → triaging          (in-flight)
                  → triaged           (report rendered + side-effects done)
                  → triaged_empty     (no verdicts to act on)
                  → triager_failed    (any hard error)
    """
    del verbose
    try:
        _write_status_patch(
            spec_dir,
            status="triaging",
            phase=f"triager_{mode}_started",
        )

        # 1. Load verdicts.json and wrap each as a TriageCandidate.
        verdicts = _load_verdicts_or_fail(spec_dir)
        if verdicts is None:
            return False
        # RFC-0006 #75: run the VAL-3 disposable-target lane ONCE (gated — a
        # no-op until a contract declares effectful VAL-3 commands AND a target
        # backend is configured), recording findings/val3_outcome.json before the
        # verification block is read. Mandatory teardown is guaranteed inside.
        # Best-effort: never breaks triage; default keeps VAL-3 honestly not_run.
        try:
            from agents.disposable_target import record_val3
            from agents.task_contract import read_tfactory_profile

            _prof = read_tfactory_profile(spec_dir)
            _src = _load_source_meta(spec_dir)
            _vprofile = (
                _src.get("verification") if isinstance(_src, dict) else None
            ) or None
            record_val3(
                spec_dir,
                _vprofile,
                getattr(_prof, "access", None) if _prof else None,
            )
        except Exception:  # noqa: BLE001 - VAL-3 lane must never break triage
            pass
        candidates = _build_candidates(spec_dir, verdicts)

        if not candidates:
            # No verdicts at all — empty pass.
            _write_empty_report(spec_dir, mode)
            _write_status_patch(
                spec_dir,
                status="triaged_empty",
                phase="triager_no_candidates",
                committed_count=0,
                rejected_count=0,
                flagged_count=0,
                dedup_collision_count=0,
            )
            return True

        # 2. Decide catalog intent, then bucket into keepers/skipped/rejects.
        catalog = _load_catalog_from_spec(spec_dir)
        decisions = _resolve_catalog_decisions(candidates, catalog)
        keepers, skipped, rejects = _bucket_candidates(candidates, decisions)

        # 3. Dedup + rank the keepers into committed/flagged survivors.
        dedup_result, committed, flagged = _dedup_and_rank(keepers)

        # 4. Build + render the report.
        report, report_md = _render_and_write_report(
            spec_dir,
            mode,
            committed,
            flagged,
            rejects,
            skipped,
            dedup_result,
            keepers,
            decisions,
        )

        # 4b. AC fidelity: which acceptance criteria are actually verified (honest).
        ac_summary = _write_ac_fidelity(spec_dir, committed, flagged, rejects)

        # 5-6. Side-effects (both dry-run by default per the no-auto-push policy).
        findings_dir = spec_dir / "findings"
        source_meta = _load_source_meta(spec_dir)
        git_result_summary = _run_git_side_effect(
            project_dir, committed, flagged, source_meta
        )
        pr_comment_summary = _run_pr_side_effect(
            project_dir, findings_dir, source_meta, report_md
        )
        # WS1: publish the quality-gate commit status (no-op unless the
        # .tfactory.yml quality_gate block is enabled; dry-run unless
        # TFACTORY_PR_STATUS=1). Best-effort — never breaks the run.
        try:
            pr_status_summary = _run_pr_status_side_effect(
                project_dir, findings_dir, source_meta, spec_dir
            )
        except Exception as exc:  # noqa: BLE001 — gate must never break triage
            _triage_log.warning("pr_status side-effect failed: %s", exc)
            pr_status_summary = {"skipped": True, "reason": f"error: {exc}"}

        # 6b-6c. Catalog mutation + template harvest (both non-fatal side-effects).
        generated_by_task = (
            source_meta.get("spec_id") or source_meta.get("task_id") or "unknown"
        )
        _persist_catalog_mutation(
            spec_dir,
            catalog,
            keepers,
            skipped,
            decisions,
            generated_by_task,
            report.generated_at,
        )
        _maybe_harvest(spec_dir, project_dir, keepers)

        # 7. Record summaries in status.json.
        committed_count = len(committed)
        flagged_count = len(flagged)
        final_status = (
            "triaged" if (committed_count or flagged_count) else "triaged_empty"
        )
        _write_status_patch(
            spec_dir,
            status=final_status,
            phase="triager_complete",
            committed_count=committed_count,
            rejected_count=len(rejects),
            flagged_count=flagged_count,
            ac_fidelity=ac_summary,
            dedup_collision_count=len(dedup_result.collisions),
            git_writer=git_result_summary,
            pr_comment=pr_comment_summary,
            pr_status=pr_status_summary,
        )
        return True

    except Exception as exc:
        _triage_log.error("triager failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase=f"triager_{mode}_exception",
            triager_error=str(exc)[:500],
        )
        return False


def _load_source_meta(spec_dir: Path) -> dict:
    """Load context/source.json. Returns {} on absence or parse failure."""
    p = spec_dir / "context" / "source.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_empty_report(spec_dir: Path, mode: str) -> None:
    """Write empty placeholder reports for the no-candidates path."""
    from agents.triage_report import build_report, render_json, render_markdown

    empty_report = build_report(
        mode=mode,
        generated_at=_now_iso(),
        committed=[],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=0,
    )
    findings_dir = spec_dir / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "triage_report.json").write_text(render_json(empty_report))
    (findings_dir / "triage_report.md").write_text(render_markdown(empty_report))


# ─── Auto-fire scheduler ─────────────────────────────────────────────────

_BG_TRIAGER_TASKS: set[asyncio.Task] = set()


def schedule_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Triager, gated by ``TFACTORY_AUTO_TRIAGE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``TFACTORY_AUTO_TRIAGE=0`` to keep evaluator's success path from
    auto-advancing.
    """
    if os.environ.get("TFACTORY_AUTO_TRIAGE", "1") == "0":
        return None
    task = asyncio.create_task(run_triager(spec_dir, project_dir, mode=mode))
    _BG_TRIAGER_TASKS.add(task)
    task.add_done_callback(_BG_TRIAGER_TASKS.discard)
    return task

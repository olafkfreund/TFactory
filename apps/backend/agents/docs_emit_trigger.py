"""Triager hook: publish test-result docs via the vendored docs-emit core (#341).

When a run reaches a terminal status, optionally render ``triage_report.json``
into a :class:`DocBundle` (``render_test_results``) and publish it through
``emit_bundle`` to the repo target (Backstage/Confluence when configured). The
doc is keyed by the plan's ``correlation_key`` so it resolves next to the plan
it verifies — the verify side of the PARR doc trail.

Opt-in via ``TFACTORY_DOCS_EMIT`` (default off) and best-effort: any failure is
swallowed so it can never break the pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt artefact is not fatal
        return None


def maybe_emit_docs(spec_dir: Path, status: dict[str, Any]) -> list[dict] | None:
    """Render + publish the test-result doc. No-op unless ``TFACTORY_DOCS_EMIT``.

    Returns the per-target result dicts (for logging/tests), or None when the
    emit is disabled or there is nothing to publish. Never raises.
    """
    try:
        from emit.docs import emit_bundle, is_enabled, render_test_results
        from emit.docs.emit_docs import resolve_targets_for_emit

        if not is_enabled():
            return None

        triage = _load_json(spec_dir / "findings" / "triage_report.json")
        if not triage:
            return None  # nothing rendered (e.g. triaged_empty / triager_failed)

        source = _load_json(spec_dir / "context" / "source.json") or {}

        # Canonical correlation-key precedence (#249): reuse the Triager's helper.
        from agents.triager import _correlation_key

        correlation_key = _correlation_key(spec_dir, status, source)
        spec_id = status.get("spec_id") or source.get("spec_id") or spec_dir.name
        component_ref = triage.get("component_ref")

        bundle = render_test_results(
            triage,
            correlation_key=correlation_key,
            spec_id=spec_id,
            component_ref=component_ref,
        )
        repo = source.get("repo") or source.get("repo_slug")
        targets = resolve_targets_for_emit(repo=repo)
        results = emit_bundle(bundle, targets=targets)
        logger.info(
            "docs-emit published test results for %s (key=%s): %s",
            spec_id,
            correlation_key,
            results,
        )
        return results
    except Exception as exc:  # noqa: BLE001 — emitting must never break the run
        logger.warning("docs-emit failed for %s: %s", spec_dir, exc)
        return None

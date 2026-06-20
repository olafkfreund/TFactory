"""Verdicts.json validation for the Evaluator agent.

Extracted from ``agents.evaluator`` (issue #450, god-file split) so the Evaluator
entry point stays a thin orchestrator. Pure validation logic — no SDK, no Docker,
no status side-effects. ``agents.evaluator`` re-exports ``_validate_verdicts`` so
existing import paths (``from agents.evaluator import _validate_verdicts``) and
the Triager pipeline keep working unchanged.

The long original ``_validate_verdicts`` is decomposed here into a per-item
helper (``_validate_one_verdict``) — a behavior-preserving split: same checks, in
the same order, returning the same error strings.
"""

from __future__ import annotations

import contextlib
import json
import logging as _logging
from pathlib import Path

from agents.output_envelope import OutputEnvelopeError, extract_json

_eval_log = _logging.getLogger("agents.evaluator")


_VALID_VERDICTS = frozenset({"accept", "reject", "flag"})


def _loads_tolerant(text: str) -> tuple[object, bool]:
    """Parse JSON that may carry a markdown fence or trailing prose.

    Thin wrapper over the shared agent-output envelope (#96): strict parse,
    then fence-strip / first-value ``raw_decode`` / outermost brace-match.

    Returns ``(doc, salvaged)`` — ``salvaged`` is True when the lenient path
    recovered the object, so the caller can rewrite a clean file. Raises
    ``json.JSONDecodeError`` when no JSON object can be recovered (preserved
    for the existing caller).
    """
    try:
        return extract_json(text)
    except OutputEnvelopeError as exc:
        raise json.JSONDecodeError(str(exc), text or "", 0) from None


def _validate_one_verdict(
    i: int,
    v: object,
    skip_ids: frozenset[str],
) -> str | None:
    """Validate a single verdict entry. Returns an error string, or None if ok.

    Same checks, in the same order, as the inline loop body the original
    ``_validate_verdicts`` carried — extracted to keep the parent within the
    complexity bar (behavior-preserving).
    """
    if not isinstance(v, dict):
        return f"verdict[{i}] is not an object"
    if "test_id" not in v:
        return f"verdict[{i}] missing 'test_id'"
    if v.get("verdict") not in _VALID_VERDICTS:
        return (
            f"verdict[{i}] has invalid 'verdict': "
            f"{v.get('verdict')!r} (must be one of {sorted(_VALID_VERDICTS)})"
        )
    # Validate signals_summary.coverage_delta_pct when present.
    # Accepted: null (None) or a numeric value (int/float).
    # Rejected: a string (the LLM must not emit "12.3" or "N/A" as text).
    signals = v.get("signals_summary")
    if isinstance(signals, dict) and "coverage_delta_pct" in signals:
        cdp = signals["coverage_delta_pct"]
        if cdp is not None and not isinstance(cdp, (int, float)):
            return (
                f"verdict[{i}].signals_summary.coverage_delta_pct "
                f"must be a number or null, got {cdp!r}"
            )
        # Warn if the LLM emitted a numeric value for a skip-coverage test.
        test_id = v.get("test_id", "")
        if test_id in skip_ids and isinstance(cdp, (int, float)):
            _eval_log.warning(
                "verdict[%d] test_id=%r is on a skip-coverage framework "
                "but signals_summary.coverage_delta_pct=%r is numeric; "
                "the LLM should have left it null — accepting verdict anyway",
                i,
                test_id,
                cdp,
            )
    return None


def _validate_verdicts(
    path: Path,
    skip_coverage_test_ids: frozenset[str] | None = None,
) -> tuple[bool, str, int]:
    """Validate the agent's verdicts.json.

    Args:
        path: Path to the verdicts.json file to validate.
        skip_coverage_test_ids: Optional set of test IDs whose framework has
            ``coverage_strategy == "skip"``.  When provided, a numeric
            ``signals_summary.coverage_delta_pct`` on one of these tests
            triggers a WARNING (the LLM should have left it null) but the
            verdict is still **accepted** — we don't reject a verdict over a
            cosmetic mismatch.

    Returns:
        (ok, error_message, verdicts_count).
        On success: (True, "", N). On failure: (False, "reason", 0).

    Accepted values for ``signals_summary.coverage_delta_pct``:
        - ``null`` / Python ``None`` — browser lane or coverage not computed.
        - Any ``int`` or ``float`` — numeric coverage delta percentage.
        - Key absent entirely — backward-compat; treated as null.

    Rejected values:
        - A string (e.g. ``"12.3"`` or ``"N/A"``).
        - Any other non-numeric type.
    """
    _skip_ids: frozenset[str] = skip_coverage_test_ids or frozenset()

    if not path.exists():
        return False, "verdicts.json not written by agent", 0
    try:
        doc, salvaged = _loads_tolerant(path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"verdicts.json is not valid JSON: {exc}", 0
    if not isinstance(doc, dict):
        return False, "verdicts.json root is not an object", 0
    if salvaged:
        # The agent wrapped the JSON in a fence or appended trailing prose.
        # Rewrite the canonical object so the Triager (which json.loads the
        # same file) doesn't trip over it.
        _eval_log.warning(
            "verdicts.json had extra data around the JSON; rewrote the salvaged object."
        )
        with contextlib.suppress(OSError):
            path.write_text(json.dumps(doc, indent=2))
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        return False, "verdicts.json missing 'verdicts' array", 0
    for i, v in enumerate(verdicts):
        err = _validate_one_verdict(i, v, _skip_ids)
        if err is not None:
            return False, err, 0
    return True, "", len(verdicts)

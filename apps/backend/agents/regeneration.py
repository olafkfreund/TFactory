"""Select evaluator rejects that qualify for bounded test regeneration."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from agents.ac_fidelity import _AC_PREFIX

MAX_REGENERATIONS_PER_RUN = 3
MIN_QUOTE_CHARS = 12

REASON_TEST_QUALITY = "test_quality"
REASON_SPEC_CONTRADICTION = "spec_contradiction"


def normalize_quote(text: object) -> str:
    """Collapse whitespace and lowercase a quote, returning empty for non-strings."""
    return " ".join(text.split()).lower() if isinstance(text, str) else ""


@dataclass(frozen=True)
class Candidate:
    test_id: str
    reason_kind: str
    quoted_ac: str | None = None
    ac_text: str | None = None


def _vote_quote(doc: Mapping[str, Any] | None, test_id: str) -> str | None:
    rows = doc.get("verdicts") if isinstance(doc, Mapping) else None
    if not isinstance(rows, list):
        return None
    matches = [r for r in rows if isinstance(r, dict) and r.get("test_id") == test_id]
    if len(matches) != 1 or matches[0].get("verdict") != "reject":
        return None
    contradiction = matches[0].get("spec_contradiction")
    quote = contradiction.get("criterion") if isinstance(contradiction, dict) else None
    return quote if isinstance(quote, str) else None


def _contradiction_candidate(
    test_id: str,
    vote_docs: Sequence[Mapping[str, Any] | None],
    ac_texts: Sequence[str],
) -> Candidate | None:
    if len(vote_docs) <= 1:
        return None
    raw_quotes: list[str] = []
    quotes: list[str] = []
    for doc in vote_docs:
        raw_quote = _vote_quote(doc, test_id)
        if raw_quote is None:
            return None
        quote = normalize_quote(raw_quote)
        if len(quote) < MIN_QUOTE_CHARS:
            return None
        raw_quotes.append(raw_quote)
        quotes.append(quote)
    for ac_text in ac_texts:
        normalized = normalize_quote(ac_text)
        if all(quote in normalized for quote in quotes):
            return Candidate(test_id, REASON_SPEC_CONTRADICTION, raw_quotes[0], ac_text)
    return None


def _candidate(
    row: Mapping[str, Any],
    bundles: Sequence[Any],
    vote_docs: Sequence[Mapping[str, Any] | None],
    ac_texts: Sequence[str],
) -> Candidate | None:
    if not isinstance(row, dict) or row.get("verdict") != "reject":
        return None
    test_id = row.get("test_id")
    if not isinstance(test_id, str) or row.get("judged") is False:
        return None
    bundle = next((b for b in bundles if getattr(b, "test_id", None) == test_id), None)
    stability = getattr(bundle, "stability", None)
    verdict = getattr(stability, "verdict", None)
    if isinstance(verdict, Enum):
        verdict = verdict.value
    if not isinstance(verdict, str):
        return None
    if verdict in ("stable", "flaky"):
        return Candidate(test_id, REASON_TEST_QUALITY)
    if (
        verdict == "consistent_fail"
        and getattr(stability, "failure_kind", None) == "assertion"
    ):
        return _contradiction_candidate(test_id, vote_docs, ac_texts)
    return None


def eligible_candidates(
    final_verdicts: Sequence[Mapping[str, Any]],
    bundles: Sequence[Any],
    vote_docs: Sequence[Mapping[str, Any] | None],
    ac_texts: Sequence[str],
) -> list[Candidate]:
    """Return all qualifying candidates in test ID order, once per test."""
    if not isinstance(final_verdicts, Sequence):
        return []
    candidates: dict[str, Candidate] = {}
    for row in final_verdicts:
        try:
            candidate = _candidate(row, bundles, vote_docs, ac_texts)
        except Exception:  # noqa: BLE001 - malformed evidence must keep the reject
            candidate = None
        if candidate is not None:
            candidates.setdefault(candidate.test_id, candidate)
    return [candidates[test_id] for test_id in sorted(candidates)]


def select_candidates(
    final_verdicts: Sequence[Mapping[str, Any]],
    bundles: Sequence[Any],
    vote_docs: Sequence[Mapping[str, Any] | None],
    ac_texts: Sequence[str],
) -> list[Candidate]:
    """Return at most one run's worth of eligible candidates."""
    return eligible_candidates(final_verdicts, bundles, vote_docs, ac_texts)[
        :MAX_REGENERATIONS_PER_RUN
    ]


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return doc if isinstance(doc, dict) else None


def load_vote_docs(findings_dir: Path, calls: int) -> list[dict[str, Any] | None]:
    """Read per-call votes in order, with None for unavailable or invalid files."""
    return [_read_object(findings_dir / f"verdicts.vote{i}.json") for i in range(calls)]


def _acceptance_strings(doc: Mapping[str, Any]) -> list[str]:
    texts = doc.get("final_acceptance")
    return (
        [text for text in texts if isinstance(text, str)]
        if isinstance(texts, list)
        else []
    )


def load_ac_texts(spec_dir: Path) -> list[str]:
    """Read criterion text from the plan and the accompanying task contract."""
    from agents.task_contract import (  # noqa: PLC0415 - load only for file I/O
        read_task_contract,
    )

    plan = _read_object(spec_dir / "test_plan.json")
    if plan is None:
        return []
    texts: list[str] = []
    phases = plan.get("phases")
    if isinstance(phases, list):
        for phase in phases:
            name = phase.get("name") if isinstance(phase, dict) else None
            match = _AC_PREFIX.match(name) if isinstance(name, str) else None
            if match:
                texts.append(match.group(2).strip())
    texts.extend(_acceptance_strings(plan))
    try:
        contract = read_task_contract(spec_dir)
    except (OSError, ValueError, RecursionError):
        contract = None
    texts.extend(_acceptance_strings(contract or {}))
    return texts


def replacement_gate(
    row: Mapping[str, Any] | None, bundle: Any | None, lane: str | None
) -> tuple[bool, bool, str]:
    """Return whether the replacement counts, whether to cap it, and why."""
    if (
        row is None
        or row.get("judged") is False
        or row.get("verdict") not in ("accept", "flag")
    ):
        return False, False, "replacement was not judged accept or flag"
    if bundle is None:
        return False, False, "replacement bundle missing"
    stability = getattr(getattr(bundle, "stability", None), "verdict", None)
    if getattr(stability, "value", stability) != "stable":
        return False, False, "replacement was not stable"
    mutation = getattr(bundle, "mutation", None)
    verdict = "not_run" if mutation is None else getattr(mutation, "verdict", None)
    if verdict == "killed":
        return True, False, "stable replacement killed mutation"
    if verdict == "not_run" and lane in ("browser", "api", "integration", "mutation"):
        return True, True, "replacement mutation not run; verdict capped at flag"
    return False, False, f"replacement mutation ({verdict}) did not meet the lane gate"


def check_merge_invariant(
    pass1_rows: Sequence[Mapping[str, Any]],
    final_rows: Sequence[Mapping[str, Any]],
    regenerated: Sequence[Mapping[str, Any]],
) -> str | None:
    """Require a one-for-one swap of rejected rows and preserve all other rows."""
    replaced = [entry for entry in regenerated if entry.get("outcome") == "replaced"]
    if len(replaced) > MAX_REGENERATIONS_PER_RUN:
        return "replacement count exceeds the regeneration cap"

    originals = list(pass1_rows)
    remaining = list(final_rows)
    for entry in replaced:
        original = entry.get("original")
        if (
            not isinstance(original, Mapping)
            or original.get("verdict") != "reject"
            or original not in originals
        ):
            return "replacement has no matching original reject"
        replacement_id = entry.get("replacement_id")
        if not isinstance(replacement_id, str) or any(
            row.get("test_id") == replacement_id for row in pass1_rows
        ):
            return "replacement ID is missing or collides with a first-pass test"
        matches = [row for row in remaining if row.get("test_id") == replacement_id]
        if len(matches) != 1 or matches[0].get("verdict") not in ("accept", "flag"):
            return f"replacement {replacement_id} has no unique accept or flag verdict"
        originals.remove(original)
        remaining.remove(matches[0])

    for row in originals:
        if row not in remaining:
            return f"first-pass verdict missing or changed for {row.get('test_id')}"
        remaining.remove(row)
    return "unexpected rows in final verdicts" if remaining else None

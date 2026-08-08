"""Contradictory acceptance criteria — a first-class finding (#896).

"Failed" and "cannot be determined" are different verdicts. A criterion that
contradicts another cannot be satisfied by ANY test, so grading it ``unverified``
reports a property of the spec as a property of the run: it reads identically to
"the generator failed" and says nothing about why. The spec then stays wrong and
the next implementer inherits the same trap.

Today such a criterion produces a replan, then a STUCK subtask, then an
``unverified`` row. This module makes the contradiction itself the finding.

What it detects, and what it does not
-------------------------------------
Deterministically: two criteria whose prose is identical once their numeric
literals are removed, but whose literals differ. That is a *provable*
contradiction — same sentence, same subject, two different signed values — and it
is the shape of the live case that motivated #888/#896 (one criterion stating a
total of 11.76 while another states 11.75).

It deliberately does NOT try to read contradictions out of differently-worded
prose. That needs the generator's judgement, which the issue's own evidence shows
it applies (three in-cluster generations all flagged the contradiction). The
finding is a file, so any such detector can write one through
:func:`write_criterion_conflicts` and get the same reporting for free. What this
module guarantees is that the clearest case never depends on a stochastic step.

The false-positive budget is the point: requiring a byte-identical skeleton makes
a spurious conflict essentially impossible, and a spurious conflict would cap a
run's VAL claim on a spec that was fine. A miss is a gap; a misfire is a lie.

Pure detection + a small file convention (``findings/criterion_conflict.json``),
matching ``dependency_review``. The triager reads it back into the AC ledger and
the completion envelope.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FINDINGS_FILE = "criterion_conflict.json"

__all__ = [
    "CriterionConflict",
    "conflicted_ac_ids",
    "detect_criterion_conflicts",
    "read_criterion_conflicts",
    "write_criterion_conflicts",
]


@dataclass
class CriterionConflict:
    """One provably unsatisfiable pair of acceptance criteria.

    ``values`` carries the literals AS SIGNED, per criterion — the concrete
    contradiction, not a paraphrase of it, so a human can see which number the
    spec must be corrected to without re-reading the plan.
    """

    ac_id: str
    conflicting_ac_id: str
    contradiction: str
    values: dict[str, list[str]] = field(default_factory=dict)
    detected_by: str = "criterion-literal skeleton match"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _skeleton(text: str) -> str:
    """Criterion prose with its numeric literals removed and space collapsed.

    Two criteria sharing a skeleton are making the same statement about the same
    subject; if their literals differ, they cannot both hold.
    """
    from agents.criterion_literals import _NUMBER  # noqa: PLC0415 - lazy by design

    return " ".join(_NUMBER.sub(" ", text or "").lower().split())


def _literals(text: str) -> dict[Decimal, str]:
    """``{Decimal value: token as written}`` for the criterion's numeric values."""
    from agents.criterion_literals import (  # noqa: PLC0415 - lazy by design
        criterion_literals,
    )

    literals: dict[Decimal, str] = criterion_literals(text or "")
    return literals


def detect_criterion_conflicts(
    criteria: list[tuple[str, str]],
) -> list[CriterionConflict]:
    """Find provably unsatisfiable pairs among ``(ac_id, text)`` criteria.

    A pair conflicts when their skeletons match exactly and their literal SETS
    differ. Pure; never raises. Each unordered pair is reported once, from the
    earlier criterion's point of view.
    """
    prepared: list[tuple[str, str, dict[Decimal, str]]] = []
    for ac_id, text in criteria or []:
        if not ac_id or not (text or "").strip():
            continue
        try:
            literals = _literals(text)
        except Exception as exc:  # noqa: BLE001 - detection must never break a run
            logger.debug("criterion literal extraction failed for %s: %s", ac_id, exc)
            continue
        if not literals:
            continue  # nothing signed to contradict
        prepared.append((ac_id, _skeleton(text), literals))

    conflicts: list[CriterionConflict] = []
    for i, (ac_id, skeleton, literals) in enumerate(prepared):
        for other_id, other_skeleton, other_literals in prepared[i + 1 :]:
            if skeleton != other_skeleton or not skeleton:
                continue
            if set(literals) == set(other_literals):
                continue  # same statement, same values — a duplicate, not a clash
            mine = sorted(literals.values())
            theirs = sorted(other_literals.values())
            conflicts.append(
                CriterionConflict(
                    ac_id=ac_id,
                    conflicting_ac_id=other_id,
                    contradiction=(
                        f"{ac_id} and {other_id} state the same requirement with "
                        f"different values ({', '.join(mine)} vs "
                        f"{', '.join(theirs)}); no test can satisfy both"
                    ),
                    values={ac_id: mine, other_id: theirs},
                )
            )
    return conflicts


def conflicted_ac_ids(
    conflicts: list[CriterionConflict] | list[dict[str, Any]] | None,
) -> set[str]:
    """Every ac_id named on either side of any conflict. Accepts dicts or objects."""
    ids: set[str] = set()
    for c in conflicts or []:
        record = c.to_dict() if isinstance(c, CriterionConflict) else c
        if not isinstance(record, dict):
            continue
        for key in ("ac_id", "conflicting_ac_id"):
            value = str(record.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


def write_criterion_conflicts(
    spec_dir: Path | str,
    conflicts: list[CriterionConflict] | list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Persist ``findings/criterion_conflict.json``. Best-effort; never raises.

    Writes nothing when there are no conflicts — an empty findings file would
    assert "we checked and it is clean" on runs where detection never ran.
    """
    records: list[dict[str, Any]] = [
        c.to_dict() if isinstance(c, CriterionConflict) else c for c in conflicts or []
    ]
    if not records:
        return None
    block: dict[str, Any] = {
        "status": "fail",
        "gating": True,
        "reason": (
            f"{len(records)} acceptance criterion pair(s) are mutually "
            "unsatisfiable; the specification must be corrected"
        ),
        "conflicts": records,
    }
    try:
        findings_dir = Path(spec_dir) / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / _FINDINGS_FILE).write_text(json.dumps(block, indent=2))
    except Exception as exc:  # noqa: BLE001 - evidence is best-effort
        logger.warning("criterion_conflict write failed (non-blocking): %s", exc)
        return block
    return block


def read_criterion_conflicts(spec_dir: Path | str) -> dict[str, Any] | None:
    """Read back the finding, or None when absent/unreadable."""
    try:
        path = Path(spec_dir) / "findings" / _FINDINGS_FILE
        if not path.is_file():
            return None
        block = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - a bad finding must not break the envelope
        return None
    return block if isinstance(block, dict) else None

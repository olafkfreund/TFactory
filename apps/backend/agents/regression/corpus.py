"""Regression corpus loader — RFC-0018 #484 (part 1).

Reads the persisted ``tests-catalog.json`` (the cross-run test corpus, see
:mod:`tests_catalog`) and maps each catalog entry to a *runnable unit* the
re-run executor (later parts of #484) can dispatch to the Nix-flake-per-task
k8s Job substrate.

Pure logic + a single catalog read — no execution, no network. The executor
builds on top of this; keeping the loader pure makes selection/grouping
trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests_catalog.io import load_catalog


@dataclass(frozen=True)
class CorpusEntry:
    """A single runnable test drawn from the persisted catalog.

    A projection of :class:`tests_catalog.schema.CatalogEntry` down to the
    fields the regression executor needs to re-run and record a test.
    """

    test_id: str
    test_file: str
    framework: str
    lane: str
    language: str
    covers_acs: tuple[str, ...] = ()
    target_ref: str | None = None
    # operator_locked means the Triager won't *regenerate* the test; it is
    # still re-run for regression (locking pins content, not execution).
    operator_locked: bool = False


def load_corpus(
    repo_root: Path, *, lanes: tuple[str, ...] | None = None
) -> list[CorpusEntry]:
    """Load the persisted corpus as runnable units.

    Returns an empty list when no catalog exists (never raises for a missing
    catalog — a project simply has nothing to re-run yet). When *lanes* is
    given, only entries in those lanes are returned. Order follows the
    catalog's own ordering for determinism.
    """
    catalog = load_catalog(repo_root)
    if catalog is None:
        return []

    wanted = set(lanes) if lanes is not None else None
    entries: list[CorpusEntry] = []
    for e in catalog.tests:
        if wanted is not None and e.lane not in wanted:
            continue
        entries.append(
            CorpusEntry(
                test_id=e.test_id,
                test_file=e.test_file,
                framework=e.framework,
                lane=e.lane,
                language=e.language,
                covers_acs=tuple(e.covers_acs),
                target_ref=e.target_ref or None,
                operator_locked=bool(e.operator_locked),
            )
        )
    return entries


def group_by_lane(entries: list[CorpusEntry]) -> dict[str, list[CorpusEntry]]:
    """Group corpus entries by lane, preserving per-lane order.

    The executor batches by lane (one provisioned environment per lane), so
    this is the natural unit of fan-out.
    """
    out: dict[str, list[CorpusEntry]] = {}
    for e in entries:
        out.setdefault(e.lane, []).append(e)
    return out

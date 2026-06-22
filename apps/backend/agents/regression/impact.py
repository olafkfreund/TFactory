"""Impact-based test selection — RFC-0018 #487 (part 1).

Given a change (a set of acceptance-criterion ids and/or changed test files),
select only the subset of the persisted corpus that covers it, so a regression
run re-executes the affected tests instead of everything. Falls back to the
full corpus when no usable signal is available (logged — never a silent empty
run).

Pure logic over :class:`~agents.regression.corpus.CorpusEntry`; the catalog's
``covers_acs`` is the impact signal (the executor wires ``--select`` in #487
part 2).
"""

from __future__ import annotations

import logging

from .corpus import CorpusEntry

logger = logging.getLogger(__name__)


def ac_id(ac: str) -> str:
    """Stable id of an acceptance criterion: the part before the first ``:``.

    ``"AC#1: User can log in"`` -> ``"AC#1"``; a bare string maps to itself.
    Matching on the id (not the full text) keeps selection stable when the
    criterion's wording changes — mirroring the catalog Triager's AC-id match.
    """
    return ac.split(":", 1)[0].strip()


def build_ac_index(corpus: list[CorpusEntry]) -> dict[str, list[CorpusEntry]]:
    """Reverse index: AC id -> entries covering it (corpus order preserved)."""
    index: dict[str, list[CorpusEntry]] = {}
    for entry in corpus:
        for ac in entry.covers_acs:
            index.setdefault(ac_id(ac), []).append(entry)
    return index


def select_by_acs(
    corpus: list[CorpusEntry],
    changed_acs: list[str],
    *,
    fallback_all: bool = True,
) -> list[CorpusEntry]:
    """Entries covering any of *changed_acs* (matched by AC id).

    Empty *changed_acs* -> the full corpus when *fallback_all* (logged), else [].
    """
    if not changed_acs:
        if fallback_all:
            logger.info("impact: no changed ACs given — selecting full corpus")
            return list(corpus)
        return []
    wanted = {ac_id(a) for a in changed_acs}
    return [e for e in corpus if any(ac_id(ac) in wanted for ac in e.covers_acs)]


def select_by_changed_files(
    corpus: list[CorpusEntry], changed_files: list[str]
) -> list[CorpusEntry]:
    """Entries whose own ``test_file`` is in *changed_files* (re-run touched tests)."""
    changed = set(changed_files)
    return [e for e in corpus if e.test_file in changed]


def select_impacted(
    corpus: list[CorpusEntry],
    *,
    changed_acs: list[str] | None = None,
    changed_files: list[str] | None = None,
    fallback_all: bool = True,
) -> list[CorpusEntry]:
    """Union of AC-impacted and changed-file entries, in corpus order, deduped.

    When neither signal is given, returns the full corpus if *fallback_all*
    (logged), else []. The union is order-preserving and dedup'd by ``test_id``.
    """
    if not changed_acs and not changed_files:
        if fallback_all:
            logger.info("impact: no change signal — selecting full corpus")
            return list(corpus)
        return []
    selected = {
        e.test_id for e in select_by_acs(corpus, changed_acs or [], fallback_all=False)
    }
    selected |= {
        e.test_id for e in select_by_changed_files(corpus, changed_files or [])
    }
    return [e for e in corpus if e.test_id in selected]

"""Tests for impact-based test selection — RFC-0018 #487 (part 1)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    build_ac_index,
    select_by_acs,
    select_by_changed_files,
    select_impacted,
)
from agents.regression.impact import ac_id  # noqa: E402


def _e(test_id: str, acs: tuple[str, ...], test_file: str | None = None) -> CorpusEntry:
    return CorpusEntry(
        test_id=test_id,
        test_file=test_file or f"tests/{test_id}.py",
        framework="pytest",
        lane="unit",
        language="python",
        covers_acs=acs,
    )


_CORPUS = [
    _e("login", ("AC#1: User can log in",)),
    _e("logout", ("AC#2: User can log out",)),
    _e("login_edge", ("AC#1: User can log in with edge cases",)),
    _e("profile", ("AC#3: Profile loads",)),
]


def test_ac_id_extracts_prefix():
    assert ac_id("AC#1: User can log in") == "AC#1"
    assert ac_id("bare") == "bare"
    assert ac_id("  AC#9 : spaced ") == "AC#9"


def test_build_ac_index_groups_by_id():
    index = build_ac_index(_CORPUS)
    assert {e.test_id for e in index["AC#1"]} == {"login", "login_edge"}
    assert {e.test_id for e in index["AC#2"]} == {"logout"}


def test_select_by_acs_matches_by_id_not_text():
    # changed AC text differs but the id (AC#1) matches both AC#1 tests
    sel = select_by_acs(_CORPUS, ["AC#1: reworded"])
    assert [e.test_id for e in sel] == ["login", "login_edge"]


def test_select_by_acs_empty_falls_back_to_full():
    assert [e.test_id for e in select_by_acs(_CORPUS, [])] == [
        e.test_id for e in _CORPUS
    ]
    assert select_by_acs(_CORPUS, [], fallback_all=False) == []


def test_select_by_changed_files():
    sel = select_by_changed_files(_CORPUS, ["tests/profile.py", "tests/nope.py"])
    assert [e.test_id for e in sel] == ["profile"]


def test_select_impacted_union_dedup_order():
    sel = select_impacted(
        _CORPUS,
        changed_acs=["AC#1"],
        changed_files=["tests/profile.py"],
    )
    # union of {login, login_edge} and {profile}, in corpus order, deduped
    assert [e.test_id for e in sel] == ["login", "login_edge", "profile"]


def test_select_impacted_no_signal_falls_back():
    assert [e.test_id for e in select_impacted(_CORPUS)] == [e.test_id for e in _CORPUS]
    assert select_impacted(_CORPUS, fallback_all=False) == []


def test_select_impacted_ac_only():
    sel = select_impacted(_CORPUS, changed_acs=["AC#3"])
    assert [e.test_id for e in sel] == ["profile"]

"""Tests for the regression corpus loader — RFC-0018 #484 (part 1)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    group_by_lane,
    load_corpus,
)
from tests_catalog.io import save_catalog  # noqa: E402
from tests_catalog.schema import CatalogEntry, TestsCatalog  # noqa: E402


def _entry(test_id: str, lane: str, **kw) -> CatalogEntry:
    return CatalogEntry.from_dict(
        {
            "test_id": test_id,
            "test_file": kw.get("test_file", f"tests/{test_id}.py"),
            "framework": kw.get("framework", "pytest"),
            "lane": lane,
            "language": kw.get("language", "python"),
            "covers_acs": kw.get("covers_acs", [f"AC#1: {test_id}"]),
            "generated_at": "2026-06-22T12:00:00Z",
            "generated_by_task": "demo-spec",
            "last_verdict": kw.get("last_verdict", "accept"),
            "target_ref": kw.get("target_ref", "web-staging"),
            "operator_locked": kw.get("operator_locked", False),
        }
    )


def _write_catalog(repo_root: Path, *entries: CatalogEntry) -> None:
    catalog = TestsCatalog(
        version=1, updated_at="2026-06-22T12:00:00Z", tests=tuple(entries)
    )
    save_catalog(repo_root, catalog)


# ── empty / missing ─────────────────────────────────────────────────────
def test_load_corpus_no_catalog_returns_empty(tmp_path):
    assert load_corpus(tmp_path) == []


# ── basic mapping ─────────────────────────────────────────────────────
def test_load_corpus_maps_entries(tmp_path):
    _write_catalog(
        tmp_path,
        _entry("login", "browser", framework="playwright", language="typescript"),
        _entry("api_health", "api"),
    )
    corpus = load_corpus(tmp_path)
    assert {e.test_id for e in corpus} == {"login", "api_health"}
    login = next(e for e in corpus if e.test_id == "login")
    assert isinstance(login, CorpusEntry)
    assert login.lane == "browser"
    assert login.framework == "playwright"
    assert login.language == "typescript"
    assert login.target_ref == "web-staging"
    assert login.covers_acs == ("AC#1: login",)


def test_load_corpus_carries_operator_locked_and_empty_target(tmp_path):
    _write_catalog(
        tmp_path,
        _entry("locked", "unit", operator_locked=True, target_ref=""),
    )
    e = load_corpus(tmp_path)[0]
    assert e.operator_locked is True
    assert e.target_ref is None  # empty string normalised to None


# ── lane filter ─────────────────────────────────────────────────────
def test_load_corpus_lane_filter(tmp_path):
    _write_catalog(
        tmp_path,
        _entry("u1", "unit"),
        _entry("b1", "browser"),
        _entry("a1", "api"),
    )
    only = load_corpus(tmp_path, lanes=("unit", "api"))
    assert sorted(e.test_id for e in only) == ["a1", "u1"]


# ── grouping ─────────────────────────────────────────────────────
def test_group_by_lane(tmp_path):
    _write_catalog(
        tmp_path,
        _entry("u1", "unit"),
        _entry("u2", "unit"),
        _entry("b1", "browser"),
    )
    grouped = group_by_lane(load_corpus(tmp_path))
    assert set(grouped) == {"unit", "browser"}
    assert [e.test_id for e in grouped["unit"]] == ["u1", "u2"]
    assert [e.test_id for e in grouped["browser"]] == ["b1"]


def test_group_by_lane_empty():
    assert group_by_lane([]) == {}

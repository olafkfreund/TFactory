"""Tests for the Backstage TechInsights test-quality emitter (#240, epic #232).

Pure — the network call is injected via the ``poster`` seam, so no Backstage is
touched. Covers: disabled no-op, component-ref resolution/override, fact
assembly from status + verdicts.json, the happy-path payload, and best-effort
error swallowing.
"""

from __future__ import annotations

import json

import pytest
from agents.backstage_integration import (
    _component_ref,
    build_facts,
    maybe_emit_backstage,
)


@pytest.fixture
def spec(tmp_path):
    (tmp_path / "findings").mkdir()
    (tmp_path / "context").mkdir()
    return tmp_path


def _write_verdicts(spec, **summary_extra):
    doc = {
        "verdicts": [
            {"test_id": "a", "verdict": "accept", "signals_summary": {"confidence": 0.9}},
            {"test_id": "b", "verdict": "flag", "signals_summary": {
                "confidence": 0.5, "flaky": {"classification": "flaky", "flip_rate": 0.5},
            }},
            {"test_id": "c", "verdict": "reject", "signals_summary": {"confidence": 0.1}},
        ],
        "confidence_summary": {
            "mean": 0.5, "accepted_mean": 0.9, "commit_readiness": "high",
            "count": 3, "accepted_count": 1,
        },
    }
    (spec / "findings" / "verdicts.json").write_text(json.dumps(doc))


def _status():
    return {
        "status": "triaged",
        "verdicts_count": 3,
        "committed_count": 1,
        "flagged_count": 1,
        "rejected_count": 1,
    }


# ─── component ref ───────────────────────────────────────────────────────


def test_component_ref_from_repo_slug():
    assert _component_ref({"repo_slug": "olafkfreund/AIFactory"}) == "component:default/aifactory"


def test_component_ref_override_bare_name(monkeypatch):
    monkeypatch.setenv("TFACTORY_BACKSTAGE_COMPONENT", "MyApp")
    assert _component_ref({}) == "component:default/myapp"


def test_component_ref_override_full_ref(monkeypatch):
    monkeypatch.setenv("TFACTORY_BACKSTAGE_COMPONENT", "component:default/custom")
    assert _component_ref({"repo_slug": "x/y"}) == "component:default/custom"


def test_component_ref_none_when_unknown():
    assert _component_ref({}) is None


# ─── fact assembly ───────────────────────────────────────────────────────


def test_build_facts(spec):
    _write_verdicts(spec)
    facts = build_facts(spec, _status())
    assert facts["accept_rate"] == round(1 / 3, 4)
    assert facts["accepted_mean_confidence"] == 0.9
    assert facts["mean_confidence"] == 0.5
    assert facts["commit_readiness"] == "high"
    assert facts["verdicts_count"] == 3
    assert facts["committed_count"] == 1
    assert facts["flaky_count"] == 1


def test_build_facts_no_verdicts_file(spec):
    facts = build_facts(spec, {"verdicts_count": 0})
    assert facts["accept_rate"] == 0.0
    assert facts["flaky_count"] == 0
    assert facts["commit_readiness"] == "low"


# ─── emit gating + happy path ────────────────────────────────────────────


def test_disabled_is_noop(spec, monkeypatch):
    monkeypatch.delenv("TFACTORY_BACKSTAGE_TECHINSIGHTS_URL", raising=False)
    out = maybe_emit_backstage(spec, _status(), poster=lambda *a: pytest.fail("posted"))
    assert out == {"emitted": False, "reason": "disabled"}


def test_no_component_is_noop(spec, monkeypatch):
    monkeypatch.setenv("TFACTORY_BACKSTAGE_TECHINSIGHTS_URL", "https://bs.example/api")
    out = maybe_emit_backstage(spec, _status(), poster=lambda *a: pytest.fail("posted"), source={})
    assert out["emitted"] is False
    assert out["reason"] == "no_component"


def test_happy_path_posts_payload(spec, monkeypatch):
    _write_verdicts(spec)
    monkeypatch.setenv("TFACTORY_BACKSTAGE_TECHINSIGHTS_URL", "https://bs.example/api/")
    monkeypatch.setenv("TFACTORY_BACKSTAGE_TOKEN", "secret")
    captured = {}

    def poster(url, payload, token):
        captured["url"] = url
        captured["payload"] = payload
        captured["token"] = token
        return {"ok": True}

    out = maybe_emit_backstage(
        spec, _status(), poster=poster, source={"repo_slug": "o/AIFactory"}
    )
    assert out["emitted"] is True
    assert out["entity"] == "component:default/aifactory"
    assert captured["url"] == "https://bs.example/api"  # trailing slash stripped
    assert captured["token"] == "secret"
    p = captured["payload"]
    assert p["entityRef"] == "component:default/aifactory"
    assert p["factName"] == "tfactory.test_quality"
    assert p["facts"]["committed_count"] == 1
    assert "timestamp" in p


def test_poster_error_is_swallowed(spec, monkeypatch):
    monkeypatch.setenv("TFACTORY_BACKSTAGE_TECHINSIGHTS_URL", "https://bs.example/api")

    def boom(url, payload, token):
        raise RuntimeError("network down")

    out = maybe_emit_backstage(
        spec, _status(), poster=boom, source={"repo_slug": "o/app"}
    )
    assert out["emitted"] is False
    assert "network down" in out["reason"]
    assert out["entity"] == "component:default/app"

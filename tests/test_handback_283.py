"""Tests for the #283 wiring: typed triage contract + manifest hash on the wire,
and the needs_human terminal completion event.

The assertion-manifest primitives themselves are covered in
``test_handback_assertion_manifest.py``; here we assert they are plumbed through
the sender and that the bounded loop's terminal emits a completion event.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.handback import build_correction_request
from agents.handback.send import send_correction

SOURCE = {
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    }
}
VERDICTS = {
    "verdicts": [
        {"test_id": "t_ok", "verdict": "accept"},
        {"test_id": "t_bad", "verdict": "reject", "reasons": ["boom"], "lane": "api"},
    ]
}
TRIAGE = {"rejected": [{"test_id": "t_bad", "test_file": "tests/test_x.py"}]}


def _seed_suite(spec_dir: Path) -> None:
    tests = spec_dir / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_x.py").write_text(
        "def test_x():\n    assert resp.status == 200\n    assert resp.ok\n"
    )


# ── #283 Part A: typed triage + manifest hash ride on the wire ───────────────


def test_confirmed_send_carries_triage_and_manifest_hash(tmp_path: Path) -> None:
    _seed_suite(tmp_path)
    captured: dict = {}

    def fake(payload):
        captured.update(payload)
        return {"success": True}

    res = send_correction(
        build_correction_request(VERDICTS, TRIAGE, SOURCE),
        tmp_path,
        dry_run=False,
        confirm=True,
        sender_fn=fake,
    )
    assert res.ok and res.sent

    # The structured typed triage contract is on the wire (what AIFactory #467
    # schema-validates), not just the markdown.
    triage = captured["triage"]
    assert triage["source"] == "triage"
    assert triage["failing_tests"][0]["test_id"] == "t_bad"
    assert triage["failing_tests"][0]["reason"] == "boom"

    # The assertion-manifest hash pins the bar and rides on both the payload and
    # the embedded triage contract.
    assert captured["manifest_hash"]
    assert triage["manifest_hash"] == captured["manifest_hash"]
    assert triage["correlation_key"] == captured["correlation_key"]


def test_send_pins_the_assertion_manifest(tmp_path: Path) -> None:
    _seed_suite(tmp_path)
    send_correction(
        build_correction_request(VERDICTS, TRIAGE, SOURCE),
        tmp_path,
        dry_run=False,
        confirm=True,
        sender_fn=lambda p: {},
    )
    pinned = json.loads((tmp_path / "findings" / "assertion_manifest.json").read_text())
    assert pinned["files"]["test_x.py"]["count"] == 2  # the failing suite's bar
    assert pinned["manifest_hash"]


def test_artifact_json_records_manifest_hash(tmp_path: Path) -> None:
    _seed_suite(tmp_path)
    send_correction(
        build_correction_request(VERDICTS, TRIAGE, SOURCE),
        tmp_path,
        dry_run=True,
        confirm=False,
        sender_fn=lambda p: {},
    )
    doc = json.loads((tmp_path / "findings" / "handback_request.json").read_text())
    assert doc["manifest_hash"]  # pinned + recorded even on the dry-run path


# ── #283 Part C: needs_human terminal emits a completion event ───────────────


def test_mark_stuck_emits_needs_human_completion(tmp_path: Path, monkeypatch) -> None:
    """The bounded loop's terminal emits a completion event (sentinel channel)
    so the cockpit learns the unit needs a human — not silent."""
    import sys

    # The route module makes apps/backend importable; import it the same way a
    # request would, then drive _mark_stuck directly.
    ws = Path(__file__).resolve().parents[1] / "apps" / "web-server"
    if str(ws) not in sys.path:
        sys.path.insert(0, str(ws))
    from server.routes.handback import _mark_stuck

    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.delenv("TFACTORY_COMPLETION_WEBHOOK", raising=False)
    (tmp_path / "status.json").write_text(
        json.dumps({"task_id": "demo:001-login", "status": "evaluating"})
    )

    _mark_stuck(tmp_path, "reached the correction-cycle cap (2)")

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["status"] == "stuck"  # backward-compat preserved
    assert status["needs_human"] is True
    # Terminal completion event fired (sentinel) with the needs_human phase.
    env = json.loads((tmp_path / "findings" / "COMPLETED.json").read_text())
    assert env["service"] == "tfactory"
    assert env["phase"] == "needs_human"
    assert isinstance(env["correlation_key"], str)


# ── #283 Part A: published, versioned schema conformance (AC) ────────────────


def test_triage_contract_is_versioned(tmp_path: Path) -> None:
    """The contract carries a version so AIFactory can reject a shape it can't read."""
    from agents.handback.send import CONTRACT_VERSION

    _seed_suite(tmp_path)
    captured: dict = {}
    send_correction(
        build_correction_request(VERDICTS, TRIAGE, SOURCE),
        tmp_path,
        dry_run=False,
        confirm=True,
        sender_fn=lambda p: captured.update(p) or {},
    )
    assert captured["triage"]["contract_version"] == CONTRACT_VERSION


def test_triage_contract_validates_against_published_schema(tmp_path: Path) -> None:
    """AC: the emitted triage report conforms to the published, versioned schema."""
    import pytest

    jsonschema = pytest.importorskip("jsonschema")

    _seed_suite(tmp_path)
    captured: dict = {}
    send_correction(
        build_correction_request(VERDICTS, TRIAGE, SOURCE),
        tmp_path,
        dry_run=False,
        confirm=True,
        sender_fn=lambda p: captured.update(p) or {},
    )
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "backend"
        / "contracts"
        / "handback-triage-contract.v1.schema.json"
    )
    jsonschema.validate(captured["triage"], json.loads(schema_path.read_text()))

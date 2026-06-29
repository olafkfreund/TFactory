"""Tests for the Triager completion callback (#85).

When the Triager reaches a terminal status, an opt-in sentinel file and an
opt-in webhook fire so the /tfactory-watch round-trip needs no polling. Both
default OFF and are strictly best-effort — a missing/failing target must never
break the pipeline.

Covered:
  - default (no env): terminal write fires neither channel
  - sentinel opt-in: terminal write → findings/COMPLETED.json with task_id+status
  - non-terminal status never fires the sentinel
  - webhook opt-in: terminal write → POST with the JSON payload
  - webhook failure is swallowed (pipeline-safe)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.triager import _write_status_patch


def _seed_status(spec_dir: Path) -> None:
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(
        json.dumps({"task_id": "042", "project_id": "demo", "status": "triaging"})
    )


def test_default_fires_no_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TFACTORY_COMPLETION_WEBHOOK", raising=False)
    monkeypatch.delenv("TFACTORY_COMPLETION_SENTINEL", raising=False)
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_sentinel_opt_in_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    marker = tmp_path / "findings" / "COMPLETED.json"
    assert marker.exists()
    body = json.loads(marker.read_text())
    assert body["task_id"] == "042"
    assert body["status"] == "triaged"


def test_non_terminal_status_does_not_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaging")  # not terminal
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_webhook_opt_in_posts_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://hook.test/notify")
    _seed_status(tmp_path)

    captured: dict = {}

    class _Resp:
        def close(self) -> None:
            pass

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _write_status_patch(tmp_path, status="triager_failed", phase="boom")

    assert captured["url"] == "http://hook.test/notify"
    assert captured["method"] == "POST"
    assert captured["body"]["task_id"] == "042"
    assert captured["body"]["status"] == "triager_failed"
    assert captured["body"]["phase"] == "boom"


def test_webhook_failure_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://hook.test/notify")
    _seed_status(tmp_path)

    def _boom(req, timeout=None):  # noqa: ANN001
        raise OSError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    # Must NOT raise — the pipeline can never break on a failed webhook.
    _write_status_patch(tmp_path, status="triaged")
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "triaged"


# ─── v1 normalized completion-event envelope (#198) ─────────────────────


def _completed_envelope(spec_dir: Path) -> dict:
    """Read the sentinel envelope after a terminal write."""
    return json.loads((spec_dir / "findings" / "COMPLETED.json").read_text())


def _seed_verdicts(spec_dir: Path, verdicts: list[dict]) -> None:
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps({"verdicts": verdicts}))


def test_envelope_carries_honest_verification_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-0006 #74: the completion envelope (and findings/verification.json)
    carry a gate-normalized VAL block; a unit+api pass is VAL-2 with VAL-3
    surfaced as a gap — never silently 'done'."""
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _seed_verdicts(tmp_path, [
        {"test_id": "u1", "lane": "unit", "verdict": "accept"},
        {"test_id": "a1", "lane": "api", "verdict": "accept"},
    ])
    _write_status_patch(
        tmp_path, status="triaged", phase="triager_complete", committed_count=2
    )
    env = _completed_envelope(tmp_path)
    block = env["verification"]
    assert block["achieved_level"] == "VAL-2"
    assert "VAL-3 not_run" in block["claim"]
    # persisted alongside the findings for the cockpit/PR surface (#76)
    disk = json.loads((tmp_path / "findings" / "verification.json").read_text())
    assert disk["achieved_level"] == "VAL-2"


def test_envelope_verification_caps_on_a_failed_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _seed_verdicts(tmp_path, [{"test_id": "u1", "lane": "unit", "verdict": "reject"}])
    _write_status_patch(tmp_path, status="triaged", phase="triager_complete")
    block = _completed_envelope(tmp_path)["verification"]
    # a failed unit lane (VAL-1) caps the honest ceiling at VAL-0
    assert block["achieved_level"] == "VAL-0"


def test_envelope_tier_raises_val_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-0011 (#444): a hard autonomy_tier raises the VAL target floor to VAL-3.

    A unit-only pass still honestly reaches only VAL-1; the higher floor merely
    surfaces a larger gap (target VAL-3) — it can never overclaim.
    """
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    (tmp_path / "context").mkdir(parents=True, exist_ok=True)
    (tmp_path / "context" / "task_contract.json").write_text(
        json.dumps({"contract_version": "2", "execution": {"autonomy_tier": "hard"}})
    )
    _seed_verdicts(tmp_path, [{"test_id": "u1", "lane": "unit", "verdict": "accept"}])
    _write_status_patch(tmp_path, status="triaged", phase="triager_complete")
    block = _completed_envelope(tmp_path)["verification"]
    assert block["target_level"] == "VAL-3"
    # the truth is still only VAL-1 — the floor cannot fake a result
    assert block["achieved_level"] == "VAL-1"


def test_envelope_absent_tier_keeps_default_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back-compat: no autonomy_tier => the default VAL-2 target is unchanged."""
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _seed_verdicts(tmp_path, [{"test_id": "u1", "lane": "unit", "verdict": "accept"}])
    _write_status_patch(tmp_path, status="triaged", phase="triager_complete")
    block = _completed_envelope(tmp_path)["verification"]
    assert block["target_level"] == "VAL-2"


def test_envelope_has_normalized_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(
        tmp_path, status="triaged", phase="triager_complete", committed_count=3
    )
    env = _completed_envelope(tmp_path)
    assert env["service"] == "tfactory"
    assert env["outcome"] == "success"
    # #471 cutover: correlation_id dropped; the chain block carries the issue.
    assert env["correlation"]["issue_number"] is None  # no issue number on this run
    assert env["result"] == {"committed_count": 3}
    # backward-compat flat fields still present
    assert env["task_id"] == "042" and env["status"] == "triaged"


# ─── RFC-0001 conformance (#211) ────────────────────────────────────────

# #471 cutover: ``time`` (CloudEvents) replaced the legacy ``updated_at`` as the
# canonical occurrence timestamp.
_RFC_CORE = {"correlation_key", "service", "task_id", "status", "phase", "time"}


def test_envelope_has_rfc0001_core_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert _RFC_CORE <= set(env)  # all six RFC fields present
    assert env["service"] == "tfactory"
    assert isinstance(env["correlation_key"], str)  # never null, always a string


def test_correlation_key_is_issue_number_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    ctx = tmp_path / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "source.json").write_text(json.dumps({"issue_number": 412}))
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert env["correlation_key"] == "412"
    assert env["correlation"]["issue_number"] == 412


def test_correlation_key_synthetic_fallback_without_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    (tmp_path / "findings").mkdir(parents=True, exist_ok=True)
    (tmp_path / "status.json").write_text(
        json.dumps({"task_id": "042", "spec_id": "spec-099", "status": "triaging"})
    )
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert env["correlation_key"] == "tf-spec-099"  # synthetic, never null
    assert env["correlation"]["issue_number"] is None


@pytest.mark.parametrize(
    "status_value,expected",
    [("triaged", "success"), ("triaged_empty", "empty"), ("triager_failed", "failure")],
)
def test_envelope_outcome_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status_value: str, expected: str
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    patch: dict = {"status": status_value}
    if status_value == "triaged":
        # A real triaged carries actionable verdicts (RFC-0001a evidence gate);
        # without any, it is not a success — see the dedicated test below.
        patch["committed_count"] = 1
    _write_status_patch(tmp_path, **patch)
    assert _completed_envelope(tmp_path)["outcome"] == expected


def test_evidence_gate_triaged_with_no_verdicts_is_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-0001a: a 'triaged' success that produced NO verdicts (none evaluated,
    accepted, or flagged) is downgraded to a failure outcome with no_evidence —
    while the internal `status` and the flag-is-attention semantics are kept."""
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")  # no verdict counts at all
    env = _completed_envelope(tmp_path)
    assert env["status"] == "triaged"          # internal status preserved
    assert env["outcome"] == "failure"          # but the outcome is not green
    assert "no_evidence" in (env.get("halt_reason") or "")
    assert env["evidence"]["proof_kind"] == "tests"


def test_evidence_gate_all_flagged_stays_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-flagged is NOT a failure — flag means 'needs human attention' by
    design and drives the handback loop. With flagged verdicts present, the
    outcome stays success (the gate only catches the no-verdict case)."""
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged", flagged_count=6, verdicts_count=6)
    env = _completed_envelope(tmp_path)
    assert env["outcome"] == "success"
    assert env["evidence"]["flagged"] == 6


def test_envelope_correlation_id_from_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    ctx = tmp_path / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "source.json").write_text(
        json.dumps({"issue_number": 412, "branch": "feat/x", "repo_slug": "o/r"})
    )
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert env["correlation"]["issue_number"] == 412
    assert env["branch"] == "feat/x" and env["repo"] == "o/r"


# ─── #282 additive envelope upgrade: id + CloudEvents-core + trace context ──

import re  # noqa: E402

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def test_envelope_keeps_legacy_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Additive: nothing the old consumers read was removed."""
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    # #471 cutover: updated_at/schema_version/event no longer emitted.
    for field in (
        "correlation_key",
        "service",
        "task_id",
        "status",
        "phase",
        "outcome",
        "correlation",
        "result",
        "usage",
        "emitted_at",
    ):
        assert field in env, field


def test_envelope_has_idempotency_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    assert _UUID_RE.match(_completed_envelope(tmp_path)["id"])


def test_envelope_has_cloudevents_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert env["specversion"] == "1.0"
    assert env["type"] == "io.factory.tfactory.completion"
    assert env["source"] == "/tfactory"
    assert env["time"]  # #471: CloudEvents time is the canonical occurrence timestamp


def test_envelope_source_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.setenv("TFACTORY_EVENT_SOURCE", "/tfactory/prod")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    assert _completed_envelope(tmp_path)["source"] == "/tfactory/prod"


def test_envelope_traceparent_is_valid_w3c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    env = _completed_envelope(tmp_path)
    assert _TRACEPARENT_RE.match(env["traceparent"])
    assert "tracestate" not in env  # omitted unless supplied


def test_envelope_validates_against_published_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parity with AIFactory: the event validates against the published
    RFC-0001/CloudEvents schema (#282)."""
    jsonschema = pytest.importorskip("jsonschema")
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    ctx = tmp_path / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "source.json").write_text(json.dumps({"issue_number": 412}))
    _write_status_patch(tmp_path, status="triaged", committed_count=2)
    env = _completed_envelope(tmp_path)
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "backend"
        / "contracts"
        / "completion-event.schema.json"
    )
    jsonschema.validate(env, json.loads(schema_path.read_text()))

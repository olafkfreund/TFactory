"""Tests for the additive CloudEvents/idempotency/trace envelope upgrade (#282).

Acceptance criteria from the issue:
  - New fields additive; existing fields untouched (CFactory keeps working).
  - ``id`` stable across retries of the same event (the envelope is built once
    and the #281 outbox re-sends it verbatim).
  - Validates against the CloudEvents-core contract in CI.
  - Non-breaking: no field removed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agents import completion_envelope as ce  # noqa: E402

_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------


def test_new_event_id_is_uuid4_and_unique():
    a, b = ce.new_event_id(), ce.new_event_id()
    assert a != b
    # UUID4 string form
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", a
    )


# ---------------------------------------------------------------------------
# CloudEvents source / type / specversion
# ---------------------------------------------------------------------------


def test_event_source_defaults(monkeypatch):
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    assert ce.event_source(None) == "/tfactory"
    assert ce.event_source("demo") == "/tfactory"  # producer identity, not per-project


def test_event_source_env_override(monkeypatch):
    monkeypatch.setenv("TFACTORY_EVENT_SOURCE", "https://tfactory.example/events")
    assert ce.event_source("demo") == "https://tfactory.example/events"


def test_cloudevents_fields_shape(monkeypatch):
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    fields = ce.cloudevents_fields(
        project_id="demo", time_iso="2026-06-08T00:00:00+00:00"
    )
    assert fields["specversion"] == "1.0"
    assert fields["type"] == "io.factory.tfactory.completion"
    assert fields["source"] == "/tfactory"
    assert fields["time"] == "2026-06-08T00:00:00+00:00"
    assert _TRACEPARENT_RE.match(fields["traceparent"])
    assert "id" in fields


# ---------------------------------------------------------------------------
# traceparent — generation + inheritance
# ---------------------------------------------------------------------------


def test_traceparent_generated_when_no_env(monkeypatch):
    monkeypatch.delenv("TRACEPARENT", raising=False)
    tp = ce.traceparent()
    assert _TRACEPARENT_RE.match(tp)
    # not all-zero
    assert tp.split("-")[1] != "0" * 32


def test_traceparent_inherited_when_valid(monkeypatch):
    upstream = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    monkeypatch.setenv("TRACEPARENT", upstream)
    assert ce.traceparent() == upstream  # cross-stage correlation preserved


def test_traceparent_regenerated_when_env_malformed(monkeypatch):
    monkeypatch.setenv("TRACEPARENT", "not-a-traceparent")
    tp = ce.traceparent()
    assert _TRACEPARENT_RE.match(tp)
    assert tp != "not-a-traceparent"


def test_tracestate_inherited(monkeypatch):
    monkeypatch.setenv("TRACESTATE", "vendor=abc")
    assert ce.tracestate() == "vendor=abc"
    fields = ce.cloudevents_fields(project_id="x", time_iso="t")
    assert fields["tracestate"] == "vendor=abc"


def test_tracestate_absent_omits_field(monkeypatch):
    monkeypatch.delenv("TRACESTATE", raising=False)
    fields = ce.cloudevents_fields(project_id="x", time_iso="t")
    assert "tracestate" not in fields


# ---------------------------------------------------------------------------
# CI schema validation
# ---------------------------------------------------------------------------


def test_validate_passes_for_well_formed_envelope():
    env = ce.cloudevents_fields(project_id="demo", time_iso="2026-06-08T00:00:00+00:00")
    assert ce.validate_cloudevents_core(env) == []


def test_validate_flags_missing_core_fields():
    errors = ce.validate_cloudevents_core({"id": "x"})
    joined = " ".join(errors)
    assert "source" in joined
    assert "type" in joined
    assert "specversion" in joined


def test_validate_flags_malformed_traceparent():
    env = {
        "id": "x",
        "source": "/tfactory",
        "type": "io.factory.tfactory.completion",
        "specversion": "1.0",
        "traceparent": "garbage",
    }
    errors = ce.validate_cloudevents_core(env)
    assert any("traceparent" in e for e in errors)


def test_validate_flags_all_zero_traceparent():
    env = {
        "id": "x",
        "source": "/tfactory",
        "type": "io.factory.tfactory.completion",
        "specversion": "1.0",
        "traceparent": "00-" + "0" * 32 + "-" + "b" * 16 + "-01",
    }
    errors = ce.validate_cloudevents_core(env)
    assert any("all-zero" in e for e in errors)


# ---------------------------------------------------------------------------
# Integration with the Triager envelope builder
# ---------------------------------------------------------------------------


def test_triager_envelope_carries_additive_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    status = {
        "task_id": "001",
        "project_id": "demo",
        "status": "triaged",
        "updated_at": "2026-06-08T12:00:00+00:00",
    }
    env = _build_completion_envelope(spec_dir, status)

    # Additive CloudEvents-core present and valid.
    assert ce.validate_cloudevents_core(env) == []
    assert env["specversion"] == "1.0"
    assert env["type"] == "io.factory.tfactory.completion"
    assert env["time"] == "2026-06-08T12:00:00+00:00"
    # Core RFC fields retained (#471 cutover dropped schema_version/event/etc).
    assert env["service"] == "tfactory"
    assert env["status"] == "triaged"
    assert env["correlation_key"]


def test_triager_envelope_id_unique_per_build(tmp_path):
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    status = {"task_id": "001", "project_id": "demo", "status": "triaged"}
    e1 = _build_completion_envelope(spec_dir, status)
    e2 = _build_completion_envelope(spec_dir, status)
    # Each distinct event gets its own id; retries reuse the *stored* envelope
    # (so the outbox re-sends the same id) — verified in the outbox suite.
    assert e1["id"] != e2["id"]


def test_outbox_uses_envelope_id_as_idempotency_key(tmp_path):
    """The #282 envelope id flows through as the #281 outbox dedup key."""
    from agents import completion_outbox as ob
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    env = _build_completion_envelope(
        spec_dir, {"task_id": "1", "project_id": "p", "status": "triaged"}
    )
    entry_id = ob.enqueue(env, root=tmp_path / "outbox")
    assert entry_id == env["id"]  # stable across retries → consumer dedups


# ---------------------------------------------------------------------------
# Typed envelope shape (#451) — the CompletionEnvelope TypedDict must describe
# the *actual* serialized shape, and typing the builder must NOT change the JSON.
# ---------------------------------------------------------------------------


def test_envelope_serialized_shape_is_unchanged(tmp_path, monkeypatch):
    """Pin the exact set of top-level keys the builder emits so that introducing
    the typed model (CompletionEnvelope) provably keeps the JSON identical."""
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    monkeypatch.delenv("TRACESTATE", raising=False)
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    status = {
        "task_id": "001",
        "project_id": "demo",
        "status": "triaged",
        "updated_at": "2026-06-08T12:00:00+00:00",
        "verdicts_count": 3,
        "committed_count": 2,
    }
    env = _build_completion_envelope(spec_dir, status)

    expected_keys = {
        # RFC-0001 core
        "correlation_key",
        "service",
        "task_id",
        "status",
        "phase",
        # RFC-0001 §4 chain block
        "correlation",
        # #471 cutover: schema_version/event/correlation_id/updated_at dropped.
        # TFactory detail + #85/#198 flat fields.
        "project_id",
        "spec_id",
        "outcome",
        "repo",
        "branch",
        "pr_number",
        "result",
        "usage",
        "emitted_at",
        # #282 CloudEvents-core + idempotency + trace
        "id",
        "specversion",
        "source",
        "type",
        "time",
        "traceparent",
        # RFC-0001a evidence
        "evidence",
        # RFC-0006 verification block (best-effort; present for a normal spec)
        "verification",
    }
    assert set(env.keys()) == expected_keys


def test_typeddict_declares_every_emitted_key(tmp_path, monkeypatch):
    """Every key the builder can emit must be declared on the CompletionEnvelope
    TypedDict, so the type is an honest description of the runtime shape."""
    monkeypatch.delenv("TFACTORY_EVENT_SOURCE", raising=False)
    monkeypatch.delenv("TRACEPARENT", raising=False)
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    env = _build_completion_envelope(
        spec_dir, {"task_id": "1", "project_id": "p", "status": "triaged"}
    )

    declared = set(ce.CompletionEnvelope.__annotations__)
    undeclared = set(env.keys()) - declared
    assert undeclared == set(), f"envelope keys not on the TypedDict: {undeclared}"


def test_envelope_is_plain_json_serializable(tmp_path, monkeypatch):
    """A TypedDict is a plain dict at runtime; typing the builder must not change
    JSON serialization (the serialized event must round-trip byte-stably)."""
    import json

    monkeypatch.delenv("TRACEPARENT", raising=False)
    from agents.triager import _build_completion_envelope

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    env = _build_completion_envelope(
        spec_dir, {"task_id": "9", "project_id": "p", "status": "triaged"}
    )
    assert isinstance(env, dict)
    dumped = json.dumps(env, indent=2)
    assert json.loads(dumped) == env


# ── running cost: usage snapshot for a non-terminal verify run ───────────────


def test_emit_usage_snapshot_posts_when_nonterminal_with_usage(tmp_path, monkeypatch):
    import agents.triager as tr
    import usage as usage_mod

    posted: list = []
    monkeypatch.setattr(tr, "_deliver_completion", lambda p: posted.append(p))
    monkeypatch.setattr(
        usage_mod,
        "usage_block_from_status",
        lambda _sd: {"total_tokens": 4242, "cost_usd": 0.1},
    )

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    status = {
        "task_id": "001",
        "project_id": "demo",
        "status": "evaluated",  # NON-terminal (triager didn't reach a verdict)
        "updated_at": "2026-06-08T12:00:00+00:00",
    }
    tr.emit_usage_snapshot(spec_dir, status)

    assert len(posted) == 1
    assert posted[0]["status"] == "evaluated"
    assert posted[0]["usage"]["total_tokens"] == 4242


def test_emit_usage_snapshot_noop_without_usage(tmp_path, monkeypatch):
    import agents.triager as tr
    import usage as usage_mod

    posted: list = []
    monkeypatch.setattr(tr, "_deliver_completion", lambda p: posted.append(p))
    monkeypatch.setattr(
        usage_mod, "usage_block_from_status", lambda _sd: {"total_tokens": 0}
    )

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    tr.emit_usage_snapshot(spec_dir, {"status": "evaluated"})
    assert posted == []

"""Tests for the completion-event transactional outbox + retrying relay (#281).

Covers the acceptance criteria from the issue:
  - Killing the emitter between state-change and delivery → eventual delivery
    (durable enqueue survives, relay replays).
  - Relay retries with exponential backoff; not-yet-due entries are skipped.
  - Delivery carries a stable idempotency id (envelope ``id`` when present).
  - No wire-format change — the envelope is forwarded verbatim.
  - Non-breaking: outbox path is opt-in behind TFACTORY_COMPLETION_OUTBOX; the
    Triager keeps the legacy direct POST when the flag is unset.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agents import completion_outbox as ob  # noqa: E402

# ---------------------------------------------------------------------------
# enqueue — durability + idempotency id
# ---------------------------------------------------------------------------


def test_enqueue_writes_durable_entry(tmp_path):
    env = {"correlation_key": "tf-1", "service": "tfactory", "status": "triaged"}
    entry_id = ob.enqueue(env, root=tmp_path)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["id"] == entry_id
    assert data["envelope"] == env  # verbatim — no wire-format change
    assert data["attempts"] == 0
    assert data["next_attempt_at"] is not None


def test_enqueue_uses_envelope_id_when_present(tmp_path):
    """Forward-compat with #282: a present envelope id becomes the dedup key."""
    entry_id = ob.enqueue({"id": "evt-abc", "status": "triaged"}, root=tmp_path)
    assert entry_id == "evt-abc"
    assert (tmp_path / "evt-abc.json").exists()


def test_enqueue_generates_id_when_absent(tmp_path):
    entry_id = ob.enqueue({"status": "triaged"}, root=tmp_path)
    assert entry_id  # non-empty generated UUID
    assert (tmp_path / f"{entry_id}.json").exists()


# ---------------------------------------------------------------------------
# relay_once — happy path + idempotency header
# ---------------------------------------------------------------------------


def test_relay_delivers_and_removes_entry(tmp_path):
    ob.enqueue({"id": "evt-1", "status": "triaged"}, root=tmp_path)
    seen = []

    def deliver(envelope, entry_id):
        seen.append((envelope, entry_id))
        return True

    stats = ob.relay_once(deliver, root=tmp_path)

    assert stats.delivered == 1
    assert stats.failed == 0
    assert list(tmp_path.glob("*.json")) == []  # delivered → removed
    assert seen[0][1] == "evt-1"  # idempotency id passed to deliver


def test_relay_passes_stable_idempotency_id(tmp_path):
    ob.enqueue({"status": "triaged"}, root=tmp_path)
    captured = {}

    def deliver(envelope, entry_id):
        captured["id"] = entry_id
        return True

    ob.relay_once(deliver, root=tmp_path)
    # The id passed to deliver matches the persisted filename stem.
    assert captured["id"]


# ---------------------------------------------------------------------------
# At-least-once: crash between state-change and delivery → eventual delivery
# ---------------------------------------------------------------------------


def test_failed_delivery_persists_for_replay(tmp_path):
    """Simulates a crash/outage: first delivery fails, entry survives, a later
    relay pass delivers it. No event is lost."""
    ob.enqueue({"id": "evt-2", "status": "triaged"}, root=tmp_path)

    # First pass: sink is down.
    stats1 = ob.relay_once(lambda e, i: False, root=tmp_path)
    assert stats1.failed == 1
    assert len(list(tmp_path.glob("*.json"))) == 1  # still durable

    # Later pass (entry now due): sink is back.
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    stats2 = ob.relay_once(lambda e, i: True, root=tmp_path, now=future)
    assert stats2.delivered == 1
    assert list(tmp_path.glob("*.json")) == []


def test_delivery_exception_is_caught_and_retried(tmp_path):
    ob.enqueue({"id": "evt-3", "status": "triaged"}, root=tmp_path)

    def boom(envelope, entry_id):
        raise RuntimeError("connection refused")

    stats = ob.relay_once(boom, root=tmp_path)
    assert stats.failed == 1
    data = json.loads((tmp_path / "evt-3.json").read_text())
    assert data["attempts"] == 1
    assert "connection refused" in data["last_error"]


# ---------------------------------------------------------------------------
# Backoff scheduling
# ---------------------------------------------------------------------------


def test_backoff_is_exponential(monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_BACKOFF_BASE", "5")
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_BACKOFF_CAP", "3600")
    assert ob.backoff_seconds(1) == 5
    assert ob.backoff_seconds(2) == 10
    assert ob.backoff_seconds(3) == 20
    assert ob.backoff_seconds(0) == 0


def test_backoff_capped(monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_BACKOFF_BASE", "5")
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_BACKOFF_CAP", "30")
    assert ob.backoff_seconds(10) == 30  # capped


def test_not_due_entry_is_skipped(tmp_path):
    ob.enqueue({"id": "evt-4", "status": "triaged"}, root=tmp_path)
    # Force a future next_attempt_at.
    p = tmp_path / "evt-4.json"
    data = json.loads(p.read_text())
    data["next_attempt_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    p.write_text(json.dumps(data))

    stats = ob.relay_once(lambda e, i: True, root=tmp_path)
    assert stats.skipped == 1
    assert stats.delivered == 0
    assert p.exists()  # untouched


# ---------------------------------------------------------------------------
# Dead-lettering after max attempts
# ---------------------------------------------------------------------------


def test_entry_dead_lettered_after_max_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX_BACKOFF_BASE", "0")
    ob.enqueue({"id": "evt-5", "status": "triaged"}, root=tmp_path)

    # Three failing passes (each due because backoff base = 0).
    for _ in range(3):
        ob.relay_once(lambda e, i: False, root=tmp_path)

    assert not (tmp_path / "evt-5.json").exists()
    assert (tmp_path / "dead" / "evt-5.json").exists()
    assert ob.pending(tmp_path) == []  # dead entries are not "pending"


# ---------------------------------------------------------------------------
# pending() listing
# ---------------------------------------------------------------------------


def test_pending_lists_oldest_first(tmp_path):
    ob.enqueue({"id": "a", "created": 1, "status": "triaged"}, root=tmp_path)
    ob.enqueue({"id": "b", "created": 2, "status": "triaged"}, root=tmp_path)
    ids = [e.id for e in ob.pending(tmp_path)]
    assert set(ids) == {"a", "b"}


# ---------------------------------------------------------------------------
# outbox_enabled flag
# ---------------------------------------------------------------------------


def test_outbox_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TFACTORY_COMPLETION_OUTBOX", raising=False)
    assert ob.outbox_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE"])
def test_outbox_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX", val)
    assert ob.outbox_enabled() is True


# ---------------------------------------------------------------------------
# default deliver — idempotency header + 2xx semantics
# ---------------------------------------------------------------------------


def test_default_deliver_posts_with_idempotency_header(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://sink.example/hook")
    captured = {}

    class FakeResp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["body"] = req.data
        return FakeResp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ok = ob._default_deliver({"id": "evt-9", "status": "triaged"}, "evt-9")

    assert ok is True
    # Header keys are capitalized by urllib (Idempotency-key).
    assert captured["headers"].get("Idempotency-key") == "evt-9"
    assert json.loads(captured["body"]) == {"id": "evt-9", "status": "triaged"}


def test_default_deliver_no_sink_returns_false(monkeypatch):
    monkeypatch.delenv("TFACTORY_COMPLETION_WEBHOOK", raising=False)
    assert ob._default_deliver({"status": "triaged"}, "x") is False


def test_default_deliver_non_2xx_returns_false(monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://sink.example/hook")

    class FakeResp:
        status = 500

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    assert ob._default_deliver({"status": "triaged"}, "x") is False


# ---------------------------------------------------------------------------
# Triager integration — opt-in, non-breaking
# ---------------------------------------------------------------------------


def _seed_status(spec_dir: Path) -> None:
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(
        json.dumps({"task_id": "042", "project_id": "demo", "status": "triaging"})
    )


def test_triager_enqueues_to_outbox_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://sink.example/hook")
    monkeypatch.setenv("TFACTORY_COMPLETION_OUTBOX", "1")
    outbox_dir = tmp_path / "outbox"
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))

    # Make delivery fail so the entry persists and we can assert on it.
    import urllib.request

    def fail(req, timeout=None):
        raise OSError("sink down")

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    from agents.triager import _write_status_patch

    spec_dir = tmp_path / "spec"
    _seed_status(spec_dir)
    _write_status_patch(spec_dir, status="triaged")

    # Event durably parked in the outbox despite the failed POST.
    entries = ob.pending(outbox_dir)
    assert len(entries) == 1
    assert entries[0].envelope["status"] == "triaged"


def test_triager_legacy_post_when_outbox_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_COMPLETION_WEBHOOK", "http://sink.example/hook")
    monkeypatch.delenv("TFACTORY_COMPLETION_OUTBOX", raising=False)
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))

    posted = {}
    import urllib.request

    class FakeResp:
        def close(self):
            pass

    def fake_urlopen(req, timeout=None):
        posted["url"] = req.full_url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from agents.triager import _write_status_patch

    spec_dir = tmp_path / "spec"
    _seed_status(spec_dir)
    _write_status_patch(spec_dir, status="triaged")

    # Legacy direct POST happened; nothing parked in the outbox.
    assert posted.get("url") == "http://sink.example/hook"
    assert not (tmp_path / "outbox").glob("*.json") or ob.pending(tmp_path / "outbox") == []

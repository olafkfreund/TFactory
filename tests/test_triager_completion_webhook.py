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


def test_default_fires_no_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TFACTORY_COMPLETION_WEBHOOK", raising=False)
    monkeypatch.delenv("TFACTORY_COMPLETION_SENTINEL", raising=False)
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_sentinel_opt_in_writes_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    marker = tmp_path / "findings" / "COMPLETED.json"
    assert marker.exists()
    body = json.loads(marker.read_text())
    assert body["task_id"] == "042"
    assert body["status"] == "triaged"


def test_non_terminal_status_does_not_fire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaging")  # not terminal
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_webhook_opt_in_posts_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_webhook_failure_is_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_envelope_has_normalized_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(
        tmp_path, status="triaged", phase="triager_complete", committed_count=3
    )
    env = _completed_envelope(tmp_path)
    assert env["schema_version"] == "1.0"
    assert env["event"] == "completion"
    assert env["service"] == "tfactory"
    assert env["outcome"] == "success"
    assert env["correlation_id"] is None  # no issue number on this run
    # RFC-0001 spine key: string, never null — synthetic fallback when no issue.
    assert env["correlation_key"] == "tf-042"
    assert env["result"] == {"committed_count": 3}
    # backward-compat flat fields still present
    assert env["task_id"] == "042" and env["status"] == "triaged"


@pytest.mark.parametrize(
    "status_value,expected",
    [("triaged", "success"), ("triaged_empty", "empty"), ("triager_failed", "failure")],
)
def test_envelope_outcome_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status_value: str, expected: str
) -> None:
    monkeypatch.setenv("TFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status=status_value)
    assert _completed_envelope(tmp_path)["outcome"] == expected


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
    assert env["correlation_id"] == 412
    # RFC-0001 spine key is the issue number as a string.
    assert env["correlation_key"] == "412"
    assert env["branch"] == "feat/x" and env["repo"] == "o/r"

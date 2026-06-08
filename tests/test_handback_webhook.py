"""Tests for the inbound AIFactory completion webhook (epic #182).

POST /api/handback/aifactory-complete — AIFactory's "fix done" signal that
auto-re-tests a TFactory task, bounded by the existing loop guard. The async
handler is called directly (house pattern: no live AIFactory, no network).

Skipped automatically in venvs without FastAPI (see tests/conftest.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make apps/web-server importable for `server.routes.handback`.
_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from fastapi import HTTPException  # noqa: E402
from server.routes import handback as hb  # noqa: E402

SECRET = "s3cret"


class _Settings:
    INBOUND_HANDBACK_ENABLED = True
    INBOUND_HANDBACK_SECRET = SECRET


def _seed(root: Path, *, status="triaged", failures=("t_bad",), source_extra=None):
    """Seed a workspace task: status.json + verdicts.json + source.json."""
    sd = root / "workspaces" / "demo" / "specs" / "001-login"
    (sd / "findings").mkdir(parents=True)
    (sd / "context").mkdir(parents=True)
    (sd / "status.json").write_text(
        json.dumps(
            {
                "status": status,
                "phase": "triager_complete",
                "lane_progress": {"unit": "complete"},
                "rerun_count": 0,
            }
        )
    )
    verdicts = [{"test_id": "t_ok", "verdict": "accept"}]
    verdicts += [{"test_id": t, "verdict": "reject"} for t in failures]
    (sd / "findings" / "verdicts.json").write_text(json.dumps({"verdicts": verdicts}))
    source = {"aifactory": {"project_id": "demo", "spec_id": "001-login"}}
    source.update(source_extra or {})
    (sd / "context" / "source.json").write_text(json.dumps(source))
    return sd


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")  # never fire the real Planner
    monkeypatch.delenv("TFACTORY_HANDBACK_MAX_CYCLES", raising=False)  # default 2
    monkeypatch.setattr(hb, "get_settings", lambda: _Settings())
    hb._RATE_LIMITER.reset()  # isolate per-task throttle between tests (#242)


def _payload(**kw):
    return hb.AIFactoryCompletePayload(tfactory_task_id="demo:001-login", **kw)


async def _call(token=SECRET, **kw):
    return await hb.aifactory_complete(_payload(**kw), x_tfactory_handback_token=token)


# ── happy path: retest ───────────────────────────────────────────────────


async def test_retest_fires_rerun(tmp_path):
    sd = _seed(tmp_path)  # cycle 0, one failing test, no prior signature
    res = await _call()
    assert res["action"] == "retest"
    assert res["cycle"] == 1
    assert res["planner_scheduled"] is False  # AUTO_PLAN=0
    status = json.loads((sd / "status.json").read_text())
    assert status["status"] == "pending" and status["phase"] == "created"
    # cycle + signature recorded for the next round's no-progress check
    source = json.loads((sd / "context" / "source.json").read_text())
    assert source["correction_cycle"] == 1
    assert source["last_failure_signature"] == ["t_bad"]


# ── bounded: stuck ───────────────────────────────────────────────────────


async def test_stuck_at_cap_does_not_rerun(tmp_path):
    sd = _seed(tmp_path, source_extra={"correction_cycle": 2})  # cap=2 reached
    res = await _call()
    assert res["action"] == "stuck"
    status = json.loads((sd / "status.json").read_text())
    assert status["status"] == "stuck" and "cap" in status["stuck_reason"]


async def test_no_progress_is_stuck(tmp_path):
    sd = _seed(
        tmp_path,
        source_extra={"correction_cycle": 1, "last_failure_signature": ["t_bad"]},
    )
    res = await _call()  # current failures == previous → no progress
    assert res["action"] == "stuck"
    assert "no progress" in res["reason"]
    assert json.loads((sd / "status.json").read_text())["status"] == "stuck"


async def test_passed_when_no_failures(tmp_path):
    _seed(tmp_path, failures=())  # all accept
    res = await _call()
    assert res["action"] == "passed"


async def test_already_running_does_not_double_fire(tmp_path):
    _seed(tmp_path, status="planning")  # a run is in flight
    res = await _call()
    assert res["action"] == "already_running"


# ── auth + correlation guards ────────────────────────────────────────────


async def test_bad_secret_is_401(tmp_path):
    _seed(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await _call(token="wrong")
    assert ei.value.status_code == 401


async def test_missing_secret_is_401(tmp_path):
    _seed(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await _call(token=None)
    assert ei.value.status_code == 401


async def test_disabled_flag_is_404(tmp_path, monkeypatch):
    _seed(tmp_path)

    class _Off:
        INBOUND_HANDBACK_ENABLED = False
        INBOUND_HANDBACK_SECRET = SECRET

    monkeypatch.setattr(hb, "get_settings", lambda: _Off())
    with pytest.raises(HTTPException) as ei:
        await _call()
    assert ei.value.status_code == 404


async def test_unknown_task_is_404(tmp_path):
    # no workspace seeded
    with pytest.raises(HTTPException) as ei:
        await _call()
    assert ei.value.status_code == 404


async def test_bad_task_id_is_400(tmp_path):
    _seed(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await hb.aifactory_complete(
            hb.AIFactoryCompletePayload(tfactory_task_id="no-colon"),
            x_tfactory_handback_token=SECRET,
        )
    assert ei.value.status_code == 400


# ── #242 hardening: rate limit + constant-time token ──────────────────────


async def test_rate_limit_returns_429(tmp_path):
    _seed(tmp_path, status="planning")  # always returns already_running → simple
    # 10 calls allowed in the window; the 11th is throttled.
    for _ in range(10):
        await _call()
    with pytest.raises(HTTPException) as ei:
        await _call()
    assert ei.value.status_code == 429


async def test_empty_token_is_401(tmp_path):
    _seed(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await _call(token="")
    assert ei.value.status_code == 401


async def test_wrong_token_is_401(tmp_path):
    _seed(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await _call(token="not-the-secret")
    assert ei.value.status_code == 401

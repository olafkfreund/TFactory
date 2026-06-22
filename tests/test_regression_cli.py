"""Tests for the regression CLI — RFC-0018 #484 (part 5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import (  # noqa: E402
    CorpusEntry,
    RegressionRequest,
    TestOutcome,
    TestStatus,
    cli,  # noqa: E402
    diff_runs,
    regression_dir,
)
from agents.regression.models import RegressionRun  # noqa: E402


# ── small parsing helpers ─────────────────────────────────────────────
def test_parse_lanes():
    assert cli.parse_lanes(None) is None
    assert cli.parse_lanes("") is None
    assert cli.parse_lanes("unit") == ("unit",)
    assert cli.parse_lanes("unit, api , browser") == ("unit", "api", "browser")


def test_now_run_id_is_deterministic_for_given_clock():
    from datetime import UTC, datetime

    run_id, ran_at = cli.now_run_id(datetime(2026, 6, 22, 13, 5, 9, tzinfo=UTC))
    assert run_id == "run-20260622T130509Z"
    assert ran_at == "2026-06-22T13:05:09Z"


def test_build_request_maps_args(tmp_path):
    args = cli._build_parser().parse_args(
        [
            "run",
            "--project",
            "myapp",
            "--repo-root",
            str(tmp_path / "repo"),
            "--workspace",
            str(tmp_path / "ws"),
            "--commit",
            "deadbeef",
            "--lanes",
            "unit,api",
            "--flaky-store",
            str(tmp_path / "hist.json"),
        ]
    )
    req = cli.build_request(args, run_id="rX", ran_at="2026-06-22T00:00:00Z")
    assert isinstance(req, RegressionRequest)
    assert req.project_id == "myapp"
    assert req.repo_root == tmp_path / "repo"
    assert req.reg_dir == regression_dir(tmp_path / "ws", "myapp")
    assert req.commit == "deadbeef"
    assert req.lanes == ("unit", "api")
    assert req.flaky_store_path == tmp_path / "hist.json"
    assert req.run_id == "rX"


# ── main exit codes (runner injected → no cluster) ───────────────────────
def _fake_run_regression_factory(has_regression: bool):
    def fake(request: RegressionRequest, runner):
        status = TestStatus.FAILED if has_regression else TestStatus.PASSED
        cur = RegressionRun(
            run_id=request.run_id,
            project_id=request.project_id,
            ran_at=request.ran_at,
            results=(TestOutcome("t", "unit", "pytest", status),),
        )
        base = RegressionRun(
            run_id="base",
            project_id=request.project_id,
            ran_at="2026-06-22T00:00:00Z",
            results=(TestOutcome("t", "unit", "pytest", TestStatus.PASSED),),
        )
        return cur, diff_runs(cur, base)

    return fake


def _argv(tmp_path) -> list[str]:
    return [
        "run",
        "--project",
        "p",
        "--repo-root",
        str(tmp_path),
        "--workspace",
        str(tmp_path / "ws"),
    ]


def test_main_exit_zero_when_clean(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "run_regression", _fake_run_regression_factory(False))
    rc = cli.main(_argv(tmp_path), runner=object())
    assert rc == 0
    assert "0 regression(s)" in capsys.readouterr().out


def test_main_exit_one_on_regression(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "run_regression", _fake_run_regression_factory(True))
    rc = cli.main(_argv(tmp_path), runner=object())
    assert rc == 1
    assert "1 regression(s)" in capsys.readouterr().out


def test_main_requires_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])

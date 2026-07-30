"""Per-commit coverage lookup and record (#851, #861).

The behaviour worth locking on the GET is that **absent coverage is never a
404**. A 404 is what a missing endpoint looks like, and for months a missing
endpoint was indistinguishable from missing data -- the workflow read both as
"N/A" and hung a commit status at pending forever. Every no-data path here
returns 200 with a null figure and a stated reason.

The POST inverts that deliberately (#861): a write to a repo TFactory does not
know is a 404. The GET's null-plus-reason exists to disambiguate "no data" from
"no endpoint"; a write has no such ambiguity, and a 200 for a figure that went
nowhere would leave the producer believing it recorded something it did not.

Until the POST existed, nothing ever wrote a point the GET could serve for
TFactory's own commits -- which is why every PR got "no coverage for this
commit" and the gate passed regardless.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = _WEB_SERVER.parent / "backend"
for p in (str(_WEB_SERVER), str(_BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.regression.coverage_trend import coverage_trend_path  # noqa: E402
from agents.regression.store import regression_dir  # noqa: E402
from server.routes import coverage as cov  # noqa: E402


def _call(**kw: Any) -> dict[str, Any]:
    kw.setdefault("repo", "olafkfreund/TFactory")
    kw.setdefault("sha", "")
    kw.setdefault("pr", None)
    kw.setdefault("project_id", "")
    return asyncio.run(cov.get_coverage(**kw))


_GIT = shutil.which("git") or "/usr/bin/git"


def _git(*args: str) -> None:
    """Run git with an absolute binary path (S607) and a fixed argv (S603)."""
    subprocess.run([_GIT, *args], check=True)  # noqa: S603


def _workspace(tmp_path: Path, project_id: str, remote: str) -> Path:
    """A real git checkout, so the remote is read the way production reads it."""
    ws = tmp_path / "workspaces" / project_id
    ws.mkdir(parents=True)
    _git("init", "-q", str(ws))
    _git("-C", str(ws), "remote", "add", "origin", remote)
    return ws


def _record_coverage(tmp_path: Path, project_id: str, commit: str, pct: float) -> None:
    reg = regression_dir(tmp_path, project_id)
    reg.mkdir(parents=True, exist_ok=True)
    coverage_trend_path(reg).write_text(
        json.dumps(
            {
                "points": [
                    {
                        "run_id": "r1",
                        "ran_at": "2026-07-28T00:00:00Z",
                        "coverage_pct": pct,
                        "commit": commit,
                    }
                ]
            }
        )
    )


def test_missing_sha_is_explained_not_404() -> None:
    result = _call(sha="", pr=848)
    assert result["coverage_pct"] is None
    assert "keyed on commit" in result["reason"]
    assert "PR #848" in result["reason"]


def test_unknown_repo_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    result = _call(repo="nobody/nothing", sha="deadbeefcafe")
    assert result["coverage_pct"] is None
    assert "no TFactory project has a checkout of nobody/nothing" in result["reason"]


def test_known_commit_returns_the_figure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-a", "https://github.com/olafkfreund/TFactory.git")
    _record_coverage(tmp_path, "proj-a", "abc1234", 81.5)

    result = _call(repo="olafkfreund/TFactory", sha="abc1234def5678", pr=848)
    assert result["coverage_pct"] == 81.5
    assert result["project_id"] == "proj-a"
    assert result["run_id"] == "r1"


def test_ssh_remote_matches_the_same_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout cloned over SSH must match a caller passing owner/name."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-ssh", "git@github.com:olafkfreund/TFactory.git")
    _record_coverage(tmp_path, "proj-ssh", "abc1234", 77.0)
    assert _call(repo="olafkfreund/TFactory", sha="abc1234")["coverage_pct"] == 77.0


def test_commit_without_coverage_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-a", "https://github.com/olafkfreund/TFactory.git")
    _record_coverage(tmp_path, "proj-a", "abc1234", 81.5)

    result = _call(repo="olafkfreund/TFactory", sha="9999999999", pr=849)
    assert result["coverage_pct"] is None
    assert "no regression run has recorded coverage" in result["reason"]
    # Naming the projects searched is what makes a null actionable.
    assert "proj-a" in result["reason"]


def test_project_id_bypasses_repo_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch works without any git checkout at all."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _record_coverage(tmp_path, "proj-direct", "abc1234", 64.25)
    result = _call(repo="ignored/entirely", sha="abc1234", project_id="proj-direct")
    assert result["coverage_pct"] == 64.25


# ── recording a figure: the producer half that never existed (#861) ──────


def _record(**kw: Any) -> dict[str, Any]:
    kw.setdefault("repo", "olafkfreund/TFactory")
    kw.setdefault("sha", "abc1234def5678")
    kw.setdefault("coverage_pct", 81.5)
    return asyncio.run(cov.record_commit_coverage(cov.CoverageReport(**kw)))


def test_a_recorded_figure_is_served_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: after a POST, the GET the workflow has been calling
    since epic #277 finally answers with a percentage instead of a reason."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-a", "https://github.com/olafkfreund/TFactory.git")

    written = _record(sha="0ee38301aaaa", coverage_pct=50.75)
    assert written["recorded"] is True
    assert written["project_id"] == "proj-a"

    served = _call(repo="olafkfreund/TFactory", sha="0ee38301aaaa", pr=608)
    assert served["coverage_pct"] == 50.75
    assert served.get("reason") is None


def test_recording_appends_rather_than_replacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger is a trend. A second commit must not evict the first, or the
    next PR would have no base figure to compare against."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-a", "https://github.com/olafkfreund/TFactory.git")

    _record(sha="1111111aaaa", coverage_pct=80.0)
    _record(sha="2222222bbbb", coverage_pct=82.0)

    assert _call(sha="1111111aaaa")["coverage_pct"] == 80.0
    assert _call(sha="2222222bbbb")["coverage_pct"] == 82.0


def test_provenance_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CI measurement and a regression run are both project coverage but not
    the same producer; a trend reader must be able to tell them apart."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    _workspace(tmp_path, "proj-a", "https://github.com/olafkfreund/TFactory.git")

    assert _record(sha="abc1234def")["run_id"].startswith("ci-")
    assert _record(sha="def5678abc", run_id="ci-run-42")["run_id"] == "ci-run-42"


def test_an_unknown_repo_is_a_404_not_a_silent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepting a figure into nowhere is the silent fallback (Factory#431):
    the producer would report having recorded coverage that cannot be read."""
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(HTTPException) as excinfo:
        _record(repo="nobody/nothing")
    assert excinfo.value.status_code == 404
    assert "nobody/nothing" in str(excinfo.value.detail)


@pytest.mark.parametrize(
    "bad",
    [
        {"sha": "../../etc/passwd"},
        {"sha": "not-hex-at-all"},
        {"sha": "abc"},  # too short to identify a commit
        {"coverage_pct": 101.0},
        {"coverage_pct": -1.0},
    ],
)
def test_junk_is_rejected_before_it_reaches_the_ledger(bad: dict[str, Any]) -> None:
    """The sha is matched against stored commits and echoed into messages, and
    the percentage is compared numerically. Neither is free text."""
    payload: dict[str, Any] = {
        "repo": "olafkfreund/TFactory",
        "sha": "abc1234def5678",
        "coverage_pct": 81.5,
        **bad,
    }
    with pytest.raises(ValidationError):
        cov.CoverageReport(**payload)


def test_no_path_returns_an_error_status() -> None:
    """Every branch is a 200 dict, never an exception or a 404.

    Locking the contract itself: the caller must be able to distinguish "no
    coverage" from "no endpoint", which it cannot do if this ever raises.
    """
    for kwargs in (
        {"sha": ""},
        {"repo": "nobody/nothing", "sha": "abc"},
        {"sha": "abc", "project_id": "does-not-exist"},
    ):
        out = _call(**kwargs)
        assert isinstance(out, dict)
        assert "coverage_pct" in out
        assert out["coverage_pct"] is None
        assert out.get("reason")

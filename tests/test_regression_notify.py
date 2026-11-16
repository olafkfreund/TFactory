"""AIFactory#2059: regression run -> post-merge-regression caller."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.regression import notify  # noqa: E402
from agents.regression.diff import RegressionClass, RegressionDiff  # noqa: E402
from agents.regression.models import RegressionRun  # noqa: E402


def _diff(regressed: bool) -> RegressionDiff:
    cls = RegressionClass.REGRESSION if regressed else RegressionClass.STABLE_PASS
    return RegressionDiff(run_id="r1", baseline_run_id="r0", entries=(("t1", cls),))


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    spec = tmp_path / "spec"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "source.json").write_text(
        json.dumps({"aifactory": {"task_id": "p:001", "api_url": "http://a:3101"}})
    )
    monkeypatch.setenv("TFACTORY_PUBLIC_URL", "https://tf.example/")
    return repo, spec, sha


def _call(env, sender, **over):
    repo, spec, sha = env
    commit = over.pop("commit", sha)
    run = RegressionRun(run_id="r1", project_id="proj", ran_at="t", commit=commit)
    kw = {"spec_dir": spec, "repo_root": repo, "sender": sender, **over}
    return notify.notify_aifactory(run, kw.pop("diff", _diff(True)), **kw)


def test_sends_once_on_regression(env):
    got = []
    assert _call(env, lambda u, b: got.append((u, b))) is True
    assert got == [
        (
            "http://a:3101/api/tasks/p:001/post-merge-regression",
            {
                "merge_sha": env[2],
                "run_url": "https://tf.example/api/projects/proj/regression?run=r1",
            },
        )
    ]


@pytest.mark.parametrize(
    "over",
    [
        {"diff": _diff(False)},
        {"commit": None},
        {"spec_dir": None},
        {"commit": "deadbeef"},
    ],
)
def test_skipped(env, over):
    got = []
    assert _call(env, lambda u, b: got.append(1), **over) is False
    assert got == []


def test_skipped_without_https_url(env, monkeypatch):
    monkeypatch.setenv("TFACTORY_PUBLIC_URL", "http://tf.example")
    assert _call(env, lambda u, b: pytest.fail("sent")) is False


def test_sender_error_does_not_raise(env):
    def boom(u, b):
        raise OSError("down")

    assert _call(env, boom) is False


def test_redirect_not_followed():
    req = urllib.request.Request("http://a/x")
    assert (
        notify._NoRedirect().redirect_request(
            req, None, 302, "Found", {}, "http://evil/"
        )
        is None
    )

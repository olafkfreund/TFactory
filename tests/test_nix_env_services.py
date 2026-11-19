"""#1582: sidecar services and the self-served SUT in the nix pytest Job."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.nix_env import (
    detect_serve_command,
    parse_pytest_exits,
    run_pytest_lane_via_nix,
)
from tools.runners.kube_sandbox import JobRunResult

_ENV = {
    "language": "python",
    "system_packages": [],
    "verify_commands": ["pytest -q"],
    "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
    "network": "none",
}
_MARKER = "__TF_APP_NOT_HEALTHY__"


def _setup(tmp_path, *, tfactory_yml=None, app=None, reqs=None):
    spec = tmp_path / "specs" / "1"
    (spec / "context").mkdir(parents=True)
    (spec / "context" / "task_contract.json").write_text(
        json.dumps({"contract_version": "2", "environment": _ENV})
    )
    if tfactory_yml is not None:
        (spec / "context" / "tfactory_yml.json").write_text(json.dumps(tfactory_yml))
    project = tmp_path / "proj"
    project.mkdir()
    if app:
        (project / "app").mkdir()
        (project / "app" / "main.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n"
        )
    if reqs:
        (project / "requirements.txt").write_text(reqs)
    test = project / "t_test.py"
    test.write_text("def test_ok(): assert True\n")
    return spec, project, test


class _Sandbox:
    def __init__(self, output="__PYTEST_EXIT=0\n"):
        self.output = output
        self.kw = None
        self.script = None

    def with_manifest_kw(self, **kw):
        self.kw = kw
        return self

    def run(self, commands, *, workdir=None, timeout=300):
        self.script = (Path(workdir) / "_tf_nix_job.sh").read_text()
        return JobRunResult(ok=True, exit_code=0, output=self.output)


def _run(monkeypatch, tmp_path, sandbox, **kw):
    spec_kw = kw.pop("setup", {})
    spec, project, test = _setup(tmp_path, **spec_kw)
    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: sandbox)
    return run_pytest_lane_via_nix(spec, project, test, **kw)


def test_detect_serve_command_top_level_app_package(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = object()\n")
    assert detect_serve_command(tmp_path, port=8200) == (
        "python -m uvicorn app.main:app --host 127.0.0.1 --port 8200"
    )


def test_services_become_sidecars_exports_and_waits(tmp_path, monkeypatch):
    sb = _Sandbox()
    _run(monkeypatch, tmp_path, sb, setup={"reqs": "psycopg2\nredis\n"})
    assert sb.kw == {"services": [("postgres", None), ("redis", None)]}
    assert "export DATABASE_URL=postgresql://app:app@127.0.0.1:5432/app" in sb.script
    assert "export REDIS_URL=redis://127.0.0.1:6379/0" in sb.script
    assert "/dev/tcp/127.0.0.1/5432" in sb.script


def test_no_services_leaves_script_and_sandbox_untouched(tmp_path, monkeypatch):
    sb = _Sandbox()
    _run(monkeypatch, tmp_path, sb)
    assert sb.kw is None
    assert "DATABASE_URL" not in sb.script
    assert "TF_NOT_HEALTHY" not in sb.script


def test_api_lane_self_serves_without_caller_serve_command(tmp_path, monkeypatch):
    sb = _Sandbox()
    _run(monkeypatch, tmp_path, sb, lane="api", setup={"app": True})
    assert "uvicorn app.main:app" in sb.script
    assert "export TFACTORY_TARGET_URL=http://127.0.0.1:8200" in sb.script


@pytest.mark.parametrize(
    "kw",
    [
        {"lane": "unit"},
        {"lane": "api", "extra_env": {"TFACTORY_TARGET_URL": "http://x"}},
        {
            "lane": "api",
            "setup": {
                "app": True,
                "tfactory_yml": {"targets": [{"name": "a", "base_url": "http://x"}]},
            },
        },
    ],
)
def test_no_self_serve_for_unit_lane_or_external_target(tmp_path, monkeypatch, kw):
    kw = {"setup": {"app": True}, **kw}
    sb = _Sandbox()
    _run(monkeypatch, tmp_path, sb, **kw)
    assert "uvicorn" not in sb.script


def test_unhealthy_dependency_skips_pytest_and_keeps_marker_per_run(
    tmp_path, monkeypatch
):
    sb = _Sandbox()
    _run(monkeypatch, tmp_path, sb, reruns=3, setup={"reqs": "asyncpg\n"})
    script = sb.script
    assert 'if [ -n "$TF_NOT_HEALTHY" ]; then' in script
    assert script.count("python -m pytest") == 3  # only in the else branch
    # what the skip branch prints must survive the batched parser, per run
    skip = "".join(
        f"__PYTEST_RUN={i}\n{_MARKER} dependency not healthy\n__PYTEST_EXIT=1\n"
        for i in (1, 2, 3)
    )
    runs = parse_pytest_exits(skip)
    assert [c for c, _ in runs] == [1, 1, 1]
    assert all(_MARKER in tail for _, tail in runs)
    assert f"echo {_MARKER} dependency not healthy" in script


def test_marker_in_output_reads_as_unhealthy_when_services_requested(
    tmp_path, monkeypatch
):
    sb = _Sandbox(output=f"{_MARKER} sidecar postgres not ready\n__PYTEST_EXIT=1\n")
    res = _run(monkeypatch, tmp_path, sb, setup={"reqs": "asyncpg\n"})
    assert res.stdout.rstrip().endswith("(SUT boot failed)")


def test_unhealthy_dependency_reports_skipped_mutants_not_run(tmp_path, monkeypatch):
    from agents.nix_env import parse_mut_exits

    sb = _Sandbox()
    spec, project, test = _setup(tmp_path, reqs="asyncpg\n")
    mutant = project / "m1_test.py"
    mutant.write_text("def test_ok(): assert False\n")
    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: sb)
    run_pytest_lane_via_nix(spec, project, test, reruns=3, mutant_files=[mutant])
    skip = sb.script.split("else\n")[0]
    assert "echo __MUT_RUN=1\necho __MUT_EXIT=127" in skip
    # 127 is a shell not-run code, which the evaluator maps to NOT_RUN, never KILLED
    assert parse_mut_exits("__MUT_RUN=1\n__MUT_EXIT=127\n") == [127]


def test_top_level_app_not_served_for_unrelated_spec_targets(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("app = object()\n")
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node ."}}')
    assert detect_serve_command(tmp_path, targets=["src/index.js"]) == "npm start"


def _cloud_layout(tmp_path):
    """factory-lab-cloud as ingested: no contract env, no flake, deps in app/."""
    spec = tmp_path / "specs" / "1"
    (spec / "context").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "main.py").write_text("app = object()\n")
    (project / "app" / "requirements.txt").write_text("redis==5.2.1\npsycopg[binary]\n")
    test = project / "t_test.py"
    test.write_text("def test_ok(): assert True\n")
    return spec, project, test


def test_contractless_api_repo_with_services_runs_in_the_job(tmp_path, monkeypatch):
    # #1582 live: no contract env -> host runner -> KeyError TFACTORY_TARGET_URL.
    spec, project, test = _cloud_layout(tmp_path)
    sb = _Sandbox()
    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: sb)
    res = run_pytest_lane_via_nix(spec, project, test, lane="api")
    assert res is not None
    assert "export TFACTORY_TARGET_URL=http://127.0.0.1:8200" in sb.script
    assert "uvicorn app.main:app" in sb.script


def test_contractless_unit_lane_still_uses_the_host_runner(tmp_path, monkeypatch):
    spec, project, test = _cloud_layout(tmp_path)
    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", _Sandbox)
    assert run_pytest_lane_via_nix(spec, project, test, lane="unit") is None


def test_nix_verify_mode_selects_nix_for_api_repo_with_services(tmp_path, monkeypatch):
    from agents.evaluator import _nix_verify_mode

    spec, project, _ = _cloud_layout(tmp_path)
    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "img")
    assert _nix_verify_mode(spec, project, "api") is True
    assert _nix_verify_mode(spec, project, "unit") is False

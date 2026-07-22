"""RFC-0005 Tier A: materialize a per-task flake from the contract environment."""

from __future__ import annotations

import json
from pathlib import Path

from agents.nix_env import (
    _NIX_JOB_LOCK,
    _nix_dispatch_gate,
    build_browser_job_command,
    collect_screenshots,
    detect_serve_command,
    environment_from_contract,
    is_nix_environment,
    materialize_flake,
    parse_pytest_exits,
    run_browser_evidence,
    run_pytest_lane_via_nix,
)
from tools.runners.kube_sandbox import JobRunResult

_UNIT_ENV = {
    "language": "python",
    "system_packages": [],
    "verify_commands": ["pytest -q"],
    "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
    "network": "none",
}

_BROWSER_ENV = {
    "language": "python",
    "system_packages": ["chromium"],
    "verify_commands": ["pytest -q", "playwright test"],
    "proof": {"verify": ["python --version", "playwright --version"]},
    "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
    "network": "restricted",
}


def _write_contract(spec_dir: Path, env: dict | None) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    contract = {"contract_version": "2", "tfactory": {"lanes": ["unit"]}}
    if env is not None:
        contract["environment"] = env
    (ctx / "task_contract.json").write_text(json.dumps(contract))


def test_is_nix_environment():
    assert is_nix_environment(_BROWSER_ENV)
    assert not is_nix_environment(None)
    assert not is_nix_environment({"provisioning": {"method": "image"}})


def test_environment_from_contract(tmp_path):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _BROWSER_ENV)
    env = environment_from_contract(spec)
    assert env is not None and env["network"] == "restricted"


def test_materialize_writes_generated_flake(tmp_path):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _BROWSER_ENV)
    project = tmp_path / "proj"
    project.mkdir()

    plan = materialize_flake(spec, project, env=_BROWSER_ENV)
    assert plan is not None
    flake = (project / "flake.nix").read_text()
    assert "playwright-test" in flake and "FONTCONFIG_FILE" in flake, flake
    assert plan.verify_commands == ["pytest -q", "playwright test"]
    assert plan.network == "restricted"
    argv = plan.develop_argv()
    assert argv[:3] == ["nix", "develop", f"path:{project}#default"], argv
    assert "pytest -q && playwright test" == argv[-1], argv


def test_build_browser_job_command_with_serve():
    cmds = build_browser_job_command(
        ["playwright test"], serve_command="python -m uvicorn app:app --port 8099"
    )
    assert cmds[0] == "mkdir -p shots"
    assert "uvicorn app:app" in cmds[1] and cmds[1].endswith("&")
    assert any("curl" in c and "8099" in c for c in cmds)
    assert cmds[-1] == "playwright test"


def test_build_browser_job_command_no_serve():
    cmds = build_browser_job_command(["playwright test"])
    assert cmds == ["mkdir -p shots", "playwright test"]  # no app-start steps


def test_collect_screenshots(tmp_path):
    proj = tmp_path / "proj"
    (proj / "shots").mkdir(parents=True)
    (proj / "shots" / "01.png").write_bytes(b"\x89PNG\r\n")
    (proj / "shots" / "junit.xml").write_text("<testsuites/>")
    (proj / "shots" / "ignore.txt").write_text("x")
    findings = tmp_path / "findings"
    out = collect_screenshots(proj, findings)
    names = sorted(p.name for p in out)
    assert names == ["01.png", "junit.xml"]  # .txt skipped
    assert (findings / "screenshots" / "01.png").exists()


def test_collect_screenshots_noop_when_absent(tmp_path):
    assert collect_screenshots(tmp_path / "proj", tmp_path / "findings") == []


def test_detect_serve_command_env_override(tmp_path):
    assert (
        detect_serve_command(tmp_path, {"serve_command": "custom serve"})
        == "custom serve"
    )


def test_detect_serve_command_root_app(tmp_path):
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    cmd = detect_serve_command(tmp_path, None, port=8099)
    assert cmd == "python -m uvicorn app:app --host 127.0.0.1 --port 8099"


def test_detect_serve_command_src_layout(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text("app = 1\n")
    assert detect_serve_command(tmp_path, None) == (
        "python -m uvicorn app.main:app --host 127.0.0.1 --port 8099"
    )


def test_detect_serve_command_node(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node server.js"}}')
    assert detect_serve_command(tmp_path, None) == "npm start"


def test_detect_serve_command_none(tmp_path):
    assert detect_serve_command(tmp_path, None) is None


def test_run_browser_evidence_noop_without_nix_env(tmp_path, monkeypatch):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, {"provisioning": {"method": "image"}})  # not nix
    assert run_browser_evidence(spec, tmp_path) is None


def test_run_browser_evidence_noop_when_sandbox_unconfigured(tmp_path, monkeypatch):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _BROWSER_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    # nix env present but no runner image configured -> graceful skip (None)
    assert run_browser_evidence(spec, project) is None


# ── run_pytest_lane_via_nix (RFC-0016 #469) ──────────────────────────────


def test_run_pytest_lane_via_nix_noop_without_nix_env(tmp_path):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, {"provisioning": {"method": "image"}})  # not nix
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_x(): assert True\n")
    assert run_pytest_lane_via_nix(spec, project, test_file) is None


def test_run_pytest_lane_via_nix_noop_when_sandbox_unconfigured(tmp_path, monkeypatch):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "test_x.py"
    test_file.write_text("def test_x(): assert True\n")
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    # nix env present but no runner image -> graceful skip (caller falls back)
    assert run_pytest_lane_via_nix(spec, project, test_file) is None


def test_run_pytest_lane_via_nix_result_shape(tmp_path, monkeypatch):
    """With a fake sandbox + junit, returns a DockerRunResult-shaped green result."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            captured["commands"] = commands
            captured["workdir"] = workdir
            # Simulate the Job writing junit + coverage into the staging dir.
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            (stage / "coverage.xml").write_text("<coverage/>")
            # Return the real seam-conforming result the Nix engine produces.
            return JobRunResult(
                ok=True, exit_code=0, output="1 passed\n__PYTEST_EXIT=0\n"
            )

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    res = run_pytest_lane_via_nix(
        spec, project, test_file, extra_env={"PYTHONHASHSEED": "7"}
    )
    assert res is not None
    assert res.returncode == 0 and res.ok is True
    assert res.junit_xml_path is not None and res.junit_xml_path.is_file()
    assert res.coverage_xml_path is not None and res.coverage_xml_path.is_file()
    # The Job ran the proven `nix develop path:/work#default` recipe.
    assert captured["commands"][0].startswith("nix develop path:/work#default")
    # The seed env was exported into the in-shell job script.
    script = project / "_tf_nix_job.sh"
    assert not script.exists()  # cleaned up after the run


def test_run_pytest_lane_via_nix_exports_src_pythonpath(tmp_path, monkeypatch):
    """The Job script puts <work>/src on PYTHONPATH so a src-layout package
    imports inside the hermetic Nix Job (#615)."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            # The job script still exists during run (cleaned up afterwards).
            captured["script"] = (Path(workdir) / "_tf_nix_job.sh").read_text()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output="__PYTEST_EXIT=0\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    run_pytest_lane_via_nix(spec, project, test_file)
    assert 'export PYTHONPATH="/work/src:/work' in captured["script"]
    # exported before the pytest invocation so the test process inherits it
    assert captured["script"].index("PYTHONPATH") < captured["script"].index("pytest")


def test_run_pytest_lane_via_nix_retries_on_missing_marker(tmp_path, monkeypatch):
    """A Nix build that produces no __PYTEST_EXIT marker (transient build failure)
    is retried once; the recovered result is used (#623)."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    calls = {"n": 0}

    class _FlakySandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            calls["n"] += 1
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            if calls["n"] == 1:  # transient build failure — no marker
                return JobRunResult(
                    ok=False, exit_code=1, output="[kube-sandbox] empty lane log"
                )
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(
                ok=True, exit_code=0, output="1 passed\n__PYTEST_EXIT=0\n"
            )

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FlakySandbox())
    res = run_pytest_lane_via_nix(spec, project, test_file)
    assert calls["n"] == 2  # retried once
    assert res is not None and res.returncode == 0


def test_run_pytest_lane_via_nix_no_retry_on_real_result(tmp_path, monkeypatch):
    """A real pytest result (marker present) is NOT retried, even on failure —
    a caught bug (e.g. the hardcode) must never be masked by a retry (#623)."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    calls = {"n": 0}

    class _FailSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            calls["n"] += 1
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            return JobRunResult(
                ok=False, exit_code=1, output="1 failed\n__PYTEST_EXIT=1\n"
            )

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FailSandbox())
    res = run_pytest_lane_via_nix(spec, project, test_file)
    assert calls["n"] == 1  # real failure -> no retry
    assert res is not None and res.returncode == 1


def test_run_pytest_lane_via_nix_missing_marker_is_failure(tmp_path, monkeypatch):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            return JobRunResult(ok=False, exit_code=1, output="boom (no marker line)\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    res = run_pytest_lane_via_nix(spec, project, test_file)
    # No __PYTEST_EXIT marker -> treated as failure (never a false pass).
    assert res is not None and res.returncode == 1 and res.ok is False


def test_run_pytest_lane_via_nix_api_lane_boots_sut_and_sets_target_url(
    tmp_path, monkeypatch
):
    """api lane (#612): a serve_command makes the Job boot the SUT in-pod at
    127.0.0.1:port and export TFACTORY_TARGET_URL BEFORE pytest, so the endpoint
    test reaches the running app instead of KeyError-ing on an unset URL."""
    spec = tmp_path / "specs" / "041"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "api_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            captured["script"] = (Path(workdir) / "_tf_nix_job.sh").read_text()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output="__PYTEST_EXIT=0\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    run_pytest_lane_via_nix(
        spec,
        project,
        test_file,
        serve_command="python -m uvicorn app.main:app --host 127.0.0.1 --port 8200",
        serve_port=8200,
    )
    script = captured["script"]
    assert "export TFACTORY_TARGET_URL=http://127.0.0.1:8200" in script
    assert "export APP_URL=http://127.0.0.1:8200" in script
    # app booted (backgrounded) + a readiness poll, both BEFORE pytest runs.
    assert "uvicorn app.main:app" in script and "&" in script
    assert script.index("TFACTORY_TARGET_URL") < script.index("pytest")
    assert script.index("uvicorn") < script.index("pytest")


def test_run_pytest_lane_via_nix_unit_lane_never_boots_an_app(tmp_path, monkeypatch):
    """No serve_command (unit lane) -> hermetic: no app boot, no target URL."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            captured["script"] = (Path(workdir) / "_tf_nix_job.sh").read_text()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output="__PYTEST_EXIT=0\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    run_pytest_lane_via_nix(spec, project, test_file)
    assert "TFACTORY_TARGET_URL" not in captured["script"]
    assert "uvicorn" not in captured["script"]


# ── run_gotest_lane_via_nix (Go test-execution lane, TFactory#443) ──────────

_GO_CONTRACT_ENV = {
    "language": "go",
    "toolchain": {"go": "1.22"},
    "system_packages": ["gotestsum", "gocover-cobertura"],
    "verify_commands": ["go test ./..."],
    "provisioning": {"method": "nix", "ref": "flake.nix", "generated": True},
    "network": "none",
}


def test_go_environment_prefers_contract_then_synthesizes(tmp_path):
    from agents.nix_env import go_environment

    spec = tmp_path / "specs" / "g1"
    spec.mkdir(parents=True)
    # No contract -> synthesized bare-go env carrying the JUnit/coverage tools.
    syn = go_environment(spec)
    assert syn["language"] == "go" and syn["toolchain"] == {}
    assert "gotestsum" in syn["system_packages"]
    assert syn["provisioning"]["method"] == "nix"
    # A contract declaring a go nix env wins (authoritative toolchain).
    _write_contract(spec, _GO_CONTRACT_ENV)
    assert go_environment(spec)["toolchain"] == {"go": "1.22"}
    # A non-go contract env is ignored for the go lane -> synthesize instead.
    _write_contract(spec, _UNIT_ENV)  # python
    assert go_environment(spec)["toolchain"] == {}


def test_go_module_dir_walks_up_to_go_mod(tmp_path):
    from agents.nix_env import _go_module_dir

    proj = tmp_path / "proj"
    mod = proj / "scenarios" / "go-hello"
    pkg = mod / "greeting"
    pkg.mkdir(parents=True)
    (mod / "go.mod").write_text("module example.com/hello\n")
    test_file = pkg / "greet_test.go"
    test_file.write_text("package greeting\n")
    # hint inside a nested package -> walk up to the enclosing module root.
    assert _go_module_dir(proj, test_file) == mod.resolve()
    # no hint -> shallowest go.mod under the project.
    assert _go_module_dir(proj, None) == mod.resolve()
    # no go.mod anywhere -> project root.
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _go_module_dir(bare, None) == bare.resolve()


def test_run_gotest_lane_via_nix_noop_when_sandbox_unconfigured(tmp_path, monkeypatch):
    from agents.nix_env import run_gotest_lane_via_nix

    spec = tmp_path / "specs" / "g1"
    spec.mkdir(parents=True)
    project = tmp_path / "proj"
    mod = project / "scenarios" / "go-hello"
    mod.mkdir(parents=True)
    (mod / "go.mod").write_text("module x\n")
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    # synthesized nix env present but no runner image -> graceful skip (None).
    assert run_gotest_lane_via_nix(spec, project) is None


def test_run_gotest_lane_via_nix_result_shape(tmp_path, monkeypatch):
    """With a fake sandbox + reports, returns a DockerRunResult-shaped green result."""
    from agents.nix_env import run_gotest_lane_via_nix

    spec = tmp_path / "specs" / "g1"
    spec.mkdir(parents=True)  # no contract -> synthesized go env
    project = tmp_path / "proj"
    mod = project / "scenarios" / "go-hello"
    mod.mkdir(parents=True)
    (mod / "go.mod").write_text("module example.com/hello\n")
    (mod / "greet_test.go").write_text("package main\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=600):
            captured["commands"] = commands
            captured["timeout"] = timeout
            captured["script"] = (Path(workdir) / "_tf_nix_job.sh").read_text()
            # Simulate the Job writing JUnit + Cobertura into the staging dir.
            stage = Path(workdir) / ".tf_gotest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            (stage / "coverage.xml").write_text("<coverage/>")
            return JobRunResult(ok=True, exit_code=0, output="ok\n__GOTEST_EXIT=0\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    res = run_gotest_lane_via_nix(
        spec, project, hint=mod / "greet_test.go", extra_env={"CGO_ENABLED": "0"}
    )
    assert res is not None
    assert res.returncode == 0 and res.ok is True
    assert res.junit_xml_path is not None and res.junit_xml_path.is_file()
    assert res.coverage_xml_path is not None and res.coverage_xml_path.is_file()
    # The Job ran the proven `nix develop path:/work#default` recipe.
    assert captured["commands"][0].startswith("nix develop path:/work#default")
    # The synthesized flake carries the go toolchain + tools (PR-A).
    flake = (project / "flake.nix").read_text()
    assert "pkgs.go" in flake and "gotestsum" in flake, flake
    # The job cd'd into the module subdir and ran gotestsum over ./... + cobertura.
    script = captured["script"]
    assert "cd /work/scenarios/go-hello" in script, script
    assert "gotestsum --junitfile=/work/.tf_gotest/junit.xml" in script, script
    assert "./..." in script and "gocover-cobertura" in script, script
    assert "export CGO_ENABLED='0'" in script, script
    assert not (project / "_tf_nix_job.sh").exists()  # cleaned up after the run


def test_run_gotest_lane_via_nix_missing_marker_is_failure(tmp_path, monkeypatch):
    from agents.nix_env import run_gotest_lane_via_nix

    spec = tmp_path / "specs" / "g1"
    spec.mkdir(parents=True)
    project = tmp_path / "proj"
    mod = project / "m"
    mod.mkdir(parents=True)
    (mod / "go.mod").write_text("module x\n")

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=600):
            return JobRunResult(ok=False, exit_code=1, output="boom (no marker)\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    res = run_gotest_lane_via_nix(spec, project)
    # No __GOTEST_EXIT marker -> treated as failure (never a false pass).
    assert res is not None and res.returncode == 1 and res.ok is False


def test_non_nix_env_returns_none(tmp_path):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, {"provisioning": {"method": "image"}})
    assert materialize_flake(spec, tmp_path) is None


def test_repo_owned_flake_respected_when_not_generated(tmp_path):
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    env = dict(_BROWSER_ENV, provisioning={"method": "nix", "generated": False})
    _write_contract(spec, env)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "flake.nix").write_text("# repo-owned, hand-written\n")

    plan = materialize_flake(spec, project, env=env)
    assert plan is not None and plan.generated is False
    # not overwritten
    assert (project / "flake.nix").read_text() == "# repo-owned, hand-written\n"


def test_run_pytest_lane_via_nix_holds_nix_lock_during_dispatch(tmp_path, monkeypatch):
    """The Nix Job dispatch runs under the process-wide _NIX_JOB_LOCK so
    concurrent lane Jobs don't contend on the RWO /nix-store (#623)."""
    from agents.nix_env import _NIX_JOB_LOCK

    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    seen = {}

    class _LockSensingSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            # The lock must be held while a dispatch is in flight.
            seen["locked"] = _NIX_JOB_LOCK.locked()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output="__PYTEST_EXIT=0\n")

    monkeypatch.setattr(
        "agents.nix_env.nix_runner_from_env", lambda: _LockSensingSandbox()
    )
    run_pytest_lane_via_nix(spec, project, test_file)
    assert seen.get("locked") is True  # dispatch happened under the lock
    assert not _NIX_JOB_LOCK.locked()  # and released afterwards


def test_run_pytest_lane_via_nix_isolates_shared_checkout(tmp_path, monkeypatch):
    """The lane runs against a per-run scratch COPY, never mutating the shared
    project checkout, and cleans the scratch up afterwards (#623)."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "src" / "pkg.py").write_text("x = 1\n")
    test_file = project / "src_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    captured = {}

    class _FakeSandbox:
        def run(self, commands, *, workdir=None, timeout=300):
            captured["is_scratch"] = Path(workdir).resolve() != project.resolve()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output="__PYTEST_EXIT=0\n")

    monkeypatch.setattr("agents.nix_env.nix_runner_from_env", lambda: _FakeSandbox())
    res = run_pytest_lane_via_nix(spec, project, test_file)
    assert res is not None and res.returncode == 0
    assert captured["is_scratch"] is True  # ran against a scratch, not the checkout
    # the shared checkout is never written to
    assert not (project / "flake.nix").exists()
    assert not (project / "_tf_nix_job.sh").exists()
    assert not (project / "tests").exists()
    # no scratch sibling is left behind
    assert not list(project.parent.glob("_nixrun-*"))
    # junit survives the scratch cleanup (copied off it)
    assert res.junit_xml_path is not None and res.junit_xml_path.is_file()


def test_nix_runner_from_env_honors_data_root(monkeypatch):
    """nix_runner_from_env passes TFACTORY_DATA_ROOT to the sandbox so pvc_subpath
    resolves co-mounts when the PVC is mounted at a non-default path (#623)."""
    from agents.nix_env import nix_runner_from_env

    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "img:latest")
    monkeypatch.setenv("TFACTORY_DATA_ROOT", "/work")
    sb = nix_runner_from_env()
    assert sb is not None and sb.data_root == "/work"


def test_nix_runner_from_env_default_data_root(monkeypatch):
    """Absent TFACTORY_DATA_ROOT, the sandbox keeps its control-plane default."""
    from agents.nix_env import nix_runner_from_env

    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "img:latest")
    monkeypatch.delenv("TFACTORY_DATA_ROOT", raising=False)
    sb = nix_runner_from_env()
    assert sb is not None and sb.data_root == "/home/nonroot/.tfactory"


def _mounted_pvcs(monkeypatch) -> set[str]:
    """The PVCs the nix Job manifest would actually mount, via nix_runner_from_env."""
    from agents.nix_env import nix_runner_from_env
    from tools.runners.kube_sandbox import build_job_manifest

    sb = nix_runner_from_env()
    assert sb is not None
    m = build_job_manifest(
        "t", sb.image, ["true"], repo_pvc=sb.repo_pvc, **sb.manifest_kw
    )
    vols = m["spec"]["template"]["spec"].get("volumes", [])
    return {
        v["persistentVolumeClaim"]["claimName"]
        for v in vols
        if "persistentVolumeClaim" in v
    }


def test_nix_in_image_drops_the_rwo_warm_store(monkeypatch):
    """#623: the warm PVC is RWO, so it is the mutex concurrent nix Jobs
    serialise on. With nix in the image the Job must not mount it at all."""
    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "img:latest")
    monkeypatch.setenv("TFACTORY_NIX_STORE_PVC", "tfactory-nix-store")
    monkeypatch.setenv("TFACTORY_NIX_IN_IMAGE", "true")
    assert "tfactory-nix-store" not in _mounted_pvcs(monkeypatch)


def test_warm_store_kept_when_flag_off(monkeypatch):
    """Default OFF stays warm — the de-pin must not silently drop the cache."""
    monkeypatch.setenv("TFACTORY_NIX_RUNNER_IMAGE", "img:latest")
    monkeypatch.setenv("TFACTORY_NIX_STORE_PVC", "tfactory-nix-store")
    monkeypatch.delenv("TFACTORY_NIX_IN_IMAGE", raising=False)
    assert "tfactory-nix-store" in _mounted_pvcs(monkeypatch)


def test_nix_in_image_flag_parsing(monkeypatch):
    from agents.nix_env import nix_in_image

    monkeypatch.delenv("TFACTORY_NIX_IN_IMAGE", raising=False)
    assert nix_in_image() is False
    for on in ("1", "true", "TRUE", " yes ", "on"):
        monkeypatch.setenv("TFACTORY_NIX_IN_IMAGE", on)
        assert nix_in_image() is True, on
    for off in ("", "0", "false", "no"):
        monkeypatch.setenv("TFACTORY_NIX_IN_IMAGE", off)
        assert nix_in_image() is False, off


def test_dispatch_gate_is_bounded_semaphore_when_nix_in_image(monkeypatch):
    """In-image regime has no shared PVC → gate must allow concurrency, not serialise.

    The whole point of nix-in-image is "speed for concurrency": each Job's /nix is
    image-local, so there is nothing to co-mount and nothing to serialise. If the
    gate here were the strict lock, the S x (3 + mutants) fan-out would run one Job
    at a time — the regression this guards.
    """
    import threading

    monkeypatch.setenv("TFACTORY_NIX_IN_IMAGE", "true")
    gate = _nix_dispatch_gate()
    assert gate is not _NIX_JOB_LOCK
    assert isinstance(gate, threading.BoundedSemaphore().__class__)

    # It genuinely admits more than one holder at once (a Lock would deadlock on
    # the second acquire). Hold two slots simultaneously without blocking.
    assert gate.acquire(blocking=False) is True
    assert gate.acquire(blocking=False) is True
    gate.release()
    gate.release()


def test_dispatch_gate_is_strict_lock_with_shared_pvc(monkeypatch):
    """Shared warm-store PVC is RWO → keep the one-at-a-time lock (#623)."""
    monkeypatch.delenv("TFACTORY_NIX_IN_IMAGE", raising=False)
    assert _nix_dispatch_gate() is _NIX_JOB_LOCK


# ── #776 batched stability: parse per-run codes + one-Job loop ────────────


def test_parse_pytest_exits_recovers_each_runs_code():
    """Flake detection depends on getting EVERY run's real exit code, in order."""
    out = (
        "__PYTEST_RUN=1\n1 passed\n__PYTEST_EXIT=0\n"
        "__PYTEST_RUN=2\n1 failed\n__PYTEST_EXIT=1\n"
        "__PYTEST_RUN=3\n1 passed\n__PYTEST_EXIT=0\n"
    )
    pairs = parse_pytest_exits(out)
    assert [c for c, _ in pairs] == [0, 1, 0]  # a 0/1 mix -> the run WAS flaky
    assert "1 failed" in pairs[1][1]


def test_parse_pytest_exits_missing_marker_is_failure_not_pass():
    """A run whose EXIT marker never printed (shell died mid-pass) must count as
    a failure, never a silent pass — same rule as the single-run parser."""
    out = "__PYTEST_RUN=1\nok\n__PYTEST_EXIT=0\n__PYTEST_RUN=2\nkilled\n"
    assert [c for c, _ in parse_pytest_exits(out)] == [0, 1]


def test_parse_pytest_exits_legacy_single_run_round_trips():
    """No RUN markers (the reruns=1 shape) -> one pair from the last EXIT."""
    assert parse_pytest_exits("2 passed\n__PYTEST_EXIT=0\n") == [
        (0, "2 passed\n__PYTEST_EXIT=0\n")
    ]
    assert parse_pytest_exits("boom\n__PYTEST_EXIT=1\n")[0][0] == 1


def _fake_sandbox_capturing_script(captured, output):
    class _FS:
        def run(self, commands, *, workdir=None, timeout=300):
            captured["script"] = (Path(workdir) / "_tf_nix_job.sh").read_text()
            stage = Path(workdir) / ".tf_pytest"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "junit.xml").write_text("<testsuites/>")
            return JobRunResult(ok=True, exit_code=0, output=output)

    return _FS()


def test_reruns_gt_1_writes_one_loop_with_n_pytest_passes(tmp_path, monkeypatch):
    """reruns=3 must run pytest 3x IN ONE Job (3 RUN markers, one dev-shell)."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "t_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    cap = {}
    monkeypatch.setattr(
        "agents.nix_env.nix_runner_from_env",
        lambda: _fake_sandbox_capturing_script(
            cap,
            "__PYTEST_RUN=1\n__PYTEST_EXIT=0\n__PYTEST_RUN=2\n__PYTEST_EXIT=0\n__PYTEST_RUN=3\n__PYTEST_EXIT=0\n",
        ),
    )
    res = run_pytest_lane_via_nix(spec, project, test_file, reruns=3)
    assert res is not None
    assert cap["script"].count("__PYTEST_RUN=") == 3
    assert cap["script"].count("python -m pytest") == 3
    # Still a SINGLE dispatch (one Job), not three.
    assert parse_pytest_exits(res.stdout) == [(0, ""), (0, ""), (0, "")]


def test_reruns_1_is_byte_identical_single_run(tmp_path, monkeypatch):
    """The default reruns=1 must not change the generated script at all."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()
    test_file = project / "t_test.py"
    test_file.write_text("def test_ok(): assert True\n")

    cap = {}
    monkeypatch.setattr(
        "agents.nix_env.nix_runner_from_env",
        lambda: _fake_sandbox_capturing_script(cap, "1 passed\n__PYTEST_EXIT=0\n"),
    )
    run_pytest_lane_via_nix(spec, project, test_file)  # reruns defaults to 1
    assert "__PYTEST_RUN=" not in cap["script"]  # no loop
    assert cap["script"].count("python -m pytest") == 1
    assert "cd /work && python -m pytest" in cap["script"]


def test_materialize_writes_flake_lock_alongside_generated_flake(tmp_path):
    """#778: a generated flake ships its lock so verify Jobs skip the re-lock."""
    import json as _json

    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    _write_contract(spec, _UNIT_ENV)
    project = tmp_path / "proj"
    project.mkdir()

    materialize_flake(spec, project, env=_UNIT_ENV)
    lock = project / "flake.lock"
    assert lock.is_file(), "generated flake must ship flake.lock"
    doc = _json.loads(lock.read_text())
    from tools.runners.nix_provisioner import DEFAULT_NIXPKGS

    assert (
        doc["nodes"]["nixpkgs"]["locked"]["rev"] == DEFAULT_NIXPKGS.rsplit("/", 1)[-1]
    )


def test_materialize_does_not_write_lock_for_repo_owned_flake(tmp_path):
    """A repo that owns its flake (manifest not generated) owns its lock too."""
    spec = tmp_path / "specs" / "027"
    spec.mkdir(parents=True)
    env = {**_UNIT_ENV, "provisioning": {"method": "nix", "generated": False}}
    _write_contract(spec, env)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "flake.nix").write_text("{ outputs = _: {}; }\n")  # repo-owned

    materialize_flake(spec, project, env=env)
    assert not (project / "flake.lock").exists()  # we didn't clobber/invent one


# ─── #776 Stage 1b: batched mutation markers ────────────────────────────────


def test_parse_mut_exits_recovers_each_mutants_code():
    from agents.nix_env import parse_mut_exits

    out = (
        "__MUT_RUN=1\nfailed\n__MUT_EXIT=1\n"
        "__MUT_RUN=2\npassed\n__MUT_EXIT=0\n"
        "__MUT_RUN=3\nfailed\n__MUT_EXIT=1\n"
    )
    assert parse_mut_exits(out) == [1, 0, 1]


def test_parse_mut_exits_missing_marker_is_failure():
    """A mutant whose EXIT marker never printed (shell died) counts as 1, never a
    false 0 (which would read as SURVIVED)."""
    from agents.nix_env import parse_mut_exits

    out = "__MUT_RUN=1\n__MUT_EXIT=0\n__MUT_RUN=2\n(shell died before exit)\n"
    assert parse_mut_exits(out) == [0, 1]


def test_parse_mut_exits_empty_when_no_mutants():
    from agents.nix_env import parse_mut_exits

    assert parse_mut_exits("__PYTEST_RUN=1\n__PYTEST_EXIT=0\n") == []
    assert parse_mut_exits("") == []


def test_mut_markers_do_not_pollute_stability_parse():
    """The stability parser (keyed on __PYTEST_RUN/__PYTEST_EXIT) must ignore the
    trailing __MUT_* markers, so batching mutation into the same Job leaves the
    stability codes byte-identical."""
    from agents.nix_env import parse_mut_exits, parse_pytest_exits

    out = (
        "__PYTEST_RUN=1\n.\n__PYTEST_EXIT=0\n"
        "__PYTEST_RUN=2\n.\n__PYTEST_EXIT=0\n"
        "__PYTEST_RUN=3\n.\n__PYTEST_EXIT=0\n"
        "__MUT_RUN=1\nF\n__MUT_EXIT=1\n"
    )
    assert [c for c, _ in parse_pytest_exits(out)] == [0, 0, 0]  # stability untouched
    assert parse_mut_exits(out) == [1]


def test_build_mutants_cmd_shape():
    from agents.nix_env import _build_mutants_cmd

    cmd = _build_mutants_cmd(["m__c1.py", "m__c2.py"])
    assert "echo __MUT_RUN=1" in cmd and "echo __MUT_RUN=2" in cmd
    assert "pytest tests/m__c1.py" in cmd and "pytest tests/m__c2.py" in cmd
    assert cmd.count("echo __MUT_EXIT=$?") == 2
    assert _build_mutants_cmd([]) == ""

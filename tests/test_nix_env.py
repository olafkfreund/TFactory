"""RFC-0005 Tier A: materialize a per-task flake from the contract environment."""

from __future__ import annotations

import json
from pathlib import Path

from agents.nix_env import (
    build_browser_job_command,
    collect_screenshots,
    detect_serve_command,
    environment_from_contract,
    is_nix_environment,
    materialize_flake,
    run_browser_evidence,
)

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
    assert detect_serve_command(tmp_path, {"serve_command": "custom serve"}) == "custom serve"


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

"""No-runtime host pytest fallback (PARR test exec in k3d): mode selection + run."""

from __future__ import annotations

from pathlib import Path

from agents.evaluator import (
    _container_runtime_available,
    _ensure_host_venv,
    _host_runner_mode,
    _run_pytest_on_host,
)


def test_mode_env_overrides(monkeypatch):
    monkeypatch.setenv("TFACTORY_RUNNER_MODE", "host")
    assert _host_runner_mode() is True
    monkeypatch.setenv("TFACTORY_RUNNER_MODE", "docker")
    assert _host_runner_mode() is False


def test_mode_auto_follows_runtime(monkeypatch):
    monkeypatch.delenv("TFACTORY_RUNNER_MODE", raising=False)
    monkeypatch.setattr("agents.evaluator._container_runtime_available", lambda: False)
    assert _host_runner_mode() is True  # no runtime -> host
    monkeypatch.setattr("agents.evaluator._container_runtime_available", lambda: True)
    assert _host_runner_mode() is False


def test_container_runtime_detect(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: None)
    assert _container_runtime_available() is False
    monkeypatch.setattr("shutil.which", lambda b: "/usr/bin/" + b)
    assert _container_runtime_available() is True


def test_run_pytest_on_host_executes_and_passes(monkeypatch, tmp_path):
    # A tiny project + a passing test, run via the host path using THIS venv
    # (which already has pytest) — proves the execution path produces a result +
    # junit without a container. Skips cleanly if pytest-cov is unavailable.
    import shutil
    import sys

    proj = tmp_path / "proj"
    (proj / "tests").mkdir(parents=True)
    (proj / "requirements.txt").write_text("")
    test_file = proj / "tests" / "test_ok.py"
    test_file.write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    # Stage a scratch like the real runner does.
    scratch = tmp_path / "scratch"
    (scratch / "tests").mkdir(parents=True)
    shutil.copy2(test_file, scratch / "tests" / "test_ok.py")

    # Use the current interpreter's venv (has pytest); skip if no pytest-cov.
    try:
        import pytest_cov  # noqa: F401
    except ImportError:
        import pytest as _pt
        _pt.skip("pytest-cov not in test venv", allow_module_level=False)
        return
    # Point the host runner at THIS real venv: its bin/python activates the
    # venv (via pyvenv.cfg) so pytest + pytest-cov are importable. A bare
    # bin/python symlink to sys.executable does NOT — with no pyvenv.cfg it
    # never activates a venv, so pytest is missing ("No module named pytest").
    real_venv = Path(sys.prefix)
    monkeypatch.setattr("agents.evaluator._ensure_host_venv", lambda pd: real_venv)

    res = _run_pytest_on_host(scratch, test_file, {}, proj)
    assert res.returncode == 0
    assert res.junit_xml_path is not None and res.junit_xml_path.exists()

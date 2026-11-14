"""Per-site checks: agent and repository-code children get no control-plane secret.

Assertions are on sentinel VALUES, not names (#3708).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_APPS = Path(__file__).parent.parent / "apps"
for _p in (_APPS / "backend", _APPS / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CONTROL = (
    "APP_API_TOKEN",
    "APP_JWT_SECRET",
    "DATABASE_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)
CLAUDE = "CLAUDE_CODE_OAUTH_TOKEN"
SENTINELS = {name: f"sentinel-{name.lower()}-value" for name in (*CONTROL, CLAUDE)}


@pytest.fixture(autouse=True)
def seeded(monkeypatch):
    for name, value in SENTINELS.items():
        monkeypatch.setenv(name, value)
    # Neutral values for the names a few sites keep on purpose.
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, f"sentinel-{name.lower()}-value")
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "sentinel-copilot-value")
    monkeypatch.setenv("HOME", str(Path(os.environ.get("TMPDIR", "/tmp"))))


def _assert_clean(env, *, claude_token: bool):
    values = set(env.values())
    leaked = {n for n in CONTROL if SENTINELS[n] in values}
    assert not leaked, f"control-plane values reached the child: {leaked}"
    assert (SENTINELS[CLAUDE] in values) is claude_token


class _Captured(Exception):
    pass


@pytest.fixture
def spawn(monkeypatch):
    """Capture ``env`` of the first asyncio subprocess spawn, then abort it."""
    seen: list = []

    async def fake(*args, **kwargs):
        seen.append(kwargs.get("env"))
        raise _Captured

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake)
    monkeypatch.setattr("shutil.which", lambda *a, **k: "/bin/true")
    return seen


def _drain(agen):
    async def run():
        async for _ in agen:
            pass

    with pytest.raises(_Captured):
        asyncio.run(run())


# --- SDK and MCP wiring ----------------------------------------------------


def _sdk_env(options_env):
    # The SDK builds the CLI env as {**os.environ, **options.env}.
    return {**os.environ, **options_env}


def test_simple_client_blanks_host_secrets(monkeypatch):
    import core.simple_client as sc

    captured = {}
    monkeypatch.setattr(
        sc, "ClaudeAgentOptions", lambda **kw: captured.update(kw) or SimpleNamespace()
    )
    monkeypatch.setattr(sc, "ClaudeSDKClient", lambda options: None)
    monkeypatch.setattr(sc, "require_auth_token", lambda: SENTINELS[CLAUDE])
    sc.create_simple_client()
    _assert_clean(_sdk_env(captured["env"]), claude_token=True)


def test_create_client_blanks_host_secrets(monkeypatch, tmp_path):
    import core.client as cl

    captured = {}
    monkeypatch.setattr(
        cl, "ClaudeAgentOptions", lambda **kw: captured.update(kw) or SimpleNamespace()
    )
    monkeypatch.setattr(cl, "ClaudeSDKClient", lambda options: None)
    monkeypatch.setattr(cl, "require_auth_token", lambda: SENTINELS[CLAUDE])
    cl.create_client(tmp_path, tmp_path, "claude-sonnet-4-5", agent_type="coder")
    _assert_clean(_sdk_env(captured["env"]), claude_token=True)


def test_catalog_mcp_env_drops_host_github_token():
    from core.client import _externalize_secret_env

    cfg = {
        "command": "x",
        "env": {
            "GITHUB_PERSONAL_ACCESS_TOKEN": SENTINELS["GITHUB_TOKEN"],
            "OTHER": "kept",
        },
    }
    sdk_env: dict = {}
    _externalize_secret_env(cfg, sdk_env)
    assert SENTINELS["GITHUB_TOKEN"] not in sdk_env.values()
    assert sdk_env["OTHER"] == "kept"


# --- backend agent runners -------------------------------------------------


def test_codex_agentic_env():
    from providers.codex_agentic import CodexAgenticProvider

    env = CodexAgenticProvider._build_subprocess_env()
    _assert_clean(env, claude_token=False)
    assert env["OPENAI_API_KEY"] == "sentinel-openai_api_key-value"


@pytest.mark.parametrize(
    ("module", "cls", "keep", "dropped"),
    [
        ("providers.codex", "CodexCLIProvider", "OPENAI_API_KEY", "GEMINI_API_KEY"),
        ("providers.gemini", "GeminiCLIProvider", "GEMINI_API_KEY", "OPENAI_API_KEY"),
        (
            "providers.gemini_agentic",
            "GeminiAgenticProvider",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
        ),
    ],
)
def test_cli_providers_env(spawn, module, cls, keep, dropped):
    import importlib

    provider = getattr(importlib.import_module(module), cls)()
    asyncio.run(provider.query("hi"))
    _drain(provider.receive_response())
    env = spawn[0]
    _assert_clean(env, claude_token=False)
    assert env[keep] == f"sentinel-{keep.lower()}-value"
    assert f"sentinel-{dropped.lower()}-value" not in env.values()


def test_copilot_agentic_env(spawn):
    from providers.copilot_agentic import CopilotAgenticProvider

    async def run():
        async with CopilotAgenticProvider() as provider:
            await provider.query("hi")
            async for _ in provider.receive_response():
                pass

    with pytest.raises(_Captured):
        asyncio.run(run())
    _assert_clean(spawn[0], claude_token=False)
    assert spawn[0]["COPILOT_GITHUB_TOKEN"] == "sentinel-copilot-value"


def test_executor_bash_real_process(tmp_path):
    from tools.executor import ToolExecutor

    result = asyncio.run(ToolExecutor(tmp_path).execute("Bash", {"command": "env"}))
    text = str(result.content)
    assert "PATH=" in text
    for value in SENTINELS.values():
        assert value not in text


# --- web-server agent CLIs -------------------------------------------------


def _insights_provider(name):
    import importlib

    mod = importlib.import_module(f"server.services.insights_providers.{name}_provider")
    return getattr(mod, f"{name.capitalize()}Provider")()


@pytest.mark.parametrize(
    ("name", "keep"),
    [("codex", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")],
)
def test_insights_cli_providers_env(spawn, tmp_path, name, keep):
    provider = _insights_provider(name)
    # The provider swallows the aborted spawn; only the captured env matters.
    asyncio.run(provider.send_message(tmp_path, "p", "hello", None, None, None))
    _assert_clean(spawn[0], claude_token=False)
    assert spawn[0][keep] == f"sentinel-{keep.lower()}-value"


def test_insights_claude_provider_env(spawn, tmp_path, monkeypatch):
    provider = _insights_provider("claude")
    monkeypatch.setattr(
        provider, "_resolve_claude_token", lambda: (SENTINELS[CLAUDE], None, None)
    )
    # The provider swallows the aborted spawn; only the captured env matters.
    asyncio.run(provider.send_message(tmp_path, "p", "hello", None, None, None))
    _assert_clean(spawn[0], claude_token=True)


def test_clarification_service_env(spawn, tmp_path):
    from server.services import clarification_service as cs

    result = asyncio.run(cs.generate_clarification_questions("t", "d", tmp_path))
    assert result["skip"] is True
    _assert_clean(spawn[0], claude_token=True)


# --- repository-code children ---------------------------------------------


def _fake_run(seen):
    def run(*args, **kwargs):
        seen.append(kwargs.get("env"))
        return subprocess.CompletedProcess(args, 0, "", "")

    return run


def test_preflight_check_import_env(monkeypatch, tmp_path):
    from agents.preflight_static import PreflightImport, check_import

    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(seen))
    imp = PreflightImport(module="json")
    check_import(imp, project_dir=tmp_path)
    _assert_clean(seen[0], claude_token=False)


def test_evaluator_host_pytest_env(monkeypatch, tmp_path):
    import agents.evaluator as ev

    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(seen))
    monkeypatch.setattr(ev, "_ensure_host_venv", lambda project_dir: tmp_path)
    test_file = tmp_path / "test_x.py"
    test_file.write_text("")
    ev._run_pytest_on_host(tmp_path, test_file, {"EXTRA": "1"}, tmp_path)
    _assert_clean(seen[-1], claude_token=False)
    assert seen[-1]["EXTRA"] == "1"


def test_evaluator_host_venv_pip_env(monkeypatch, tmp_path):
    import agents.evaluator as ev

    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(seen))
    monkeypatch.setattr("venv.create", lambda *a, **k: None)
    monkeypatch.setattr(ev, "_HOST_VENVS", {})
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    ev._ensure_host_venv(tmp_path)
    assert len(seen) == 5  # pytest, -r requirements.txt, three -e passes
    for env in seen:
        _assert_clean(env, claude_token=False)


def test_build_runner_command_step_env():
    from tools.runners.build_runner import run_build_steps

    seen: list = []

    def runner(argv, **kwargs):
        seen.append(kwargs.get("env"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    step = SimpleNamespace(type="command", command="true", cwd=None)
    run_build_steps([step], repo_root=Path("."), runner_fn=runner)
    _assert_clean(seen[0], claude_token=False)


def test_local_serve_runtime_env(tmp_path):
    from tools.runners.local_serve_runtime import LocalServeRuntime

    seen: list = []
    runtime = LocalServeRuntime.__new__(LocalServeRuntime)
    runtime.serve_command = "true"
    runtime.project_dir = tmp_path
    runtime._popen_fn = lambda argv, **kw: seen.append(kw.get("env"))
    runtime.start()
    _assert_clean(seen[0], claude_token=False)


def test_orchestrator_subprocess_env(monkeypatch, tmp_path):
    from services.orchestrator import ServiceOrchestrator

    seen: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(seen))
    orch = ServiceOrchestrator(tmp_path)
    assert orch._get_docker_compose_cmd() is not None
    orch._compose_file = tmp_path / "docker-compose.yml"
    orch._start_docker_compose(5)
    orch._stop_docker_compose()
    assert len(seen) >= 3
    for env in seen:
        _assert_clean(env, claude_token=False)


def test_orchestrator_local_service_popen_env(monkeypatch, tmp_path):
    from services.orchestrator import ServiceConfig, ServiceOrchestrator

    seen: list = []

    def fake_popen(argv, **kwargs):
        seen.append(kwargs.get("env"))
        raise OSError("captured")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    orch = ServiceOrchestrator(tmp_path)
    orch._services = [ServiceConfig(name="app", startup_command="true")]
    orch._start_local_services(1)
    assert seen
    _assert_clean(seen[0], claude_token=False)


def test_orchestrator_compose_keeps_docker_connection(monkeypatch, tmp_path):
    from services.orchestrator import ServiceOrchestrator

    monkeypatch.setenv("DOCKER_HOST", "tcp://docker:2376")
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    seen: list = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ServiceOrchestrator(tmp_path)._get_docker_compose_cmd() is None
    assert [argv[0] for argv, _ in seen] == ["docker", "docker-compose"]
    for _, env in seen:
        _assert_clean(env, claude_token=False)
        assert env["DOCKER_HOST"] == "tcp://docker:2376"
        assert env["DOCKER_TLS_VERIFY"] == "1"


def test_executor_grep_env(spawn, tmp_path):
    from tools.executor import ToolExecutor

    try:
        asyncio.run(ToolExecutor(tmp_path).execute("Grep", {"pattern": "x"}))
    except _Captured:
        pass
    assert spawn
    _assert_clean(spawn[0], claude_token=False)

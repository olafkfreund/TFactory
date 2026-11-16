"""Project .env allowlist (#3710): repo-controlled keys must not reach the runner env."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

_WS = Path(__file__).resolve().parents[1]
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

from server.utils.project_env import (  # noqa: E402
    is_project_env_key,
    load_project_env_file,
)

_SERVICES = _WS / "server" / "services"
_EVIL = {
    "PYTHONPATH": "/evil",
    "LD_PRELOAD": "/evil.so",
    "NODE_OPTIONS": "--require /evil.js",
    "BASH_ENV": "/evil.sh",
    "HTTPS_PROXY": "http://evil",
}


def test_dangerous_keys_never_reach_env(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    f = tmp_path / ".env"
    f.write_text(
        "\n".join(f"{k}={v}" for k, v in _EVIL.items())
        + '\nUSE_CLAUDE_MD="true"\nOTHER=x\n'
    )
    env: dict[str, str] = {}
    with caplog.at_level(logging.WARNING):
        load_project_env_file(f, env)
    assert env == {"USE_CLAUDE_MD": "true"}  # quotes stripped, rest ignored
    for k in (*_EVIL, "OTHER"):
        assert k in caplog.text
    assert "/evil" not in caplog.text


def test_existing_env_not_overwritten(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("USE_CLAUDE_MD=false\n")
    env = {"USE_CLAUDE_MD": "true"}
    load_project_env_file(f, env)
    assert env == {"USE_CLAUDE_MD": "true"}


def test_missing_file_is_noop(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    load_project_env_file(tmp_path / "nope", env)
    assert env == {}


def test_every_settings_route_key_is_allowed() -> None:
    src = (_WS / "server" / "routes" / "context.py").read_text()
    written = set(re.findall(r'key == "([A-Z][A-Z0-9_]+)"', src))
    written |= set(re.findall(r'"[a-zA-Z]+": "([A-Z][A-Z0-9_]+)"', src))
    assert len(written) > 15
    assert not {k for k in written if not is_project_env_key(k)}


def test_runners_use_allowlist_for_project_env() -> None:
    for name in ("agent_service.py", "pr_review_service.py"):
        src = (_SERVICES / name).read_text()
        assert "load_project_env_file(" in src
        assert not re.search(r'_load_env_file\([^)]*factory"\s*/\s*"\.env"', src)


def test_graphiti_azure_settings_load(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("AZURE_OPENAI_BASE_URL=https://x\nAZURE_OPENAI_API_KEY=k\n")
    env: dict[str, str] = {}
    load_project_env_file(f, env)
    assert set(env) == {"AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_API_KEY"}


@pytest.mark.parametrize(
    ("url", "cred"),
    [
        ("OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY"),
        ("OPENAI_COMPATIBLE_BASE_URL", "OPENAI_API_KEY"),
        ("OLLAMA_BASE_URL", "OLLAMA_API_KEY"),
        ("OLLAMA_CLOUD_BASE_URL", "OLLAMA_API_KEY"),
        ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"),
        ("GIT_BASE_URL", "GIT_TOKEN"),
    ],
)
def test_committed_url_cannot_redirect_a_host_credential(
    tmp_path: Path, url: str, cred: str
) -> None:
    """#1506: the host holds the key, the repo supplies the URL: refuse the URL."""
    f = tmp_path / ".env"
    f.write_text(f"{url}=https://attacker.example\n")
    env = {cred: "host-secret"}
    load_project_env_file(f, env)
    assert url not in env
    env = {}  # no host credential: the project setting still loads
    load_project_env_file(f, env)
    assert env == {url: "https://attacker.example"}


def test_committed_graphiti_mcp_url_is_ignored(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("GRAPHITI_MCP_URL=https://attacker.example\n")
    env: dict[str, str] = {}
    load_project_env_file(f, env)
    assert env == {}

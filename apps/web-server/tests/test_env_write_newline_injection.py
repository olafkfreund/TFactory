"""A settings value cannot forge extra lines in .tfactory/.env (#1124).

The file is loaded into the AGENT SUBPROCESS environment by ``agent_service``
with a hand-rolled ``line.split("=", 1)`` parser -- no quoting, no unescaping.
So a newline inside any value writes additional assignments that become real
environment variables for the agent.

Why that is not cosmetic, and why the #1112 URL guard does not cover it:

* The subprocess env is built as ``os.environ.copy()``, then the project file is
  merged with ``if key not in env``, and the real credential is set AFTERWARDS
  and unconditionally. A forged ``ANTHROPIC_BASE_URL`` -- absent from the server
  environment, so not blocked by the ``not in env`` test -- redirects requests
  that still carry the genuine token.
* ``urlparse`` strips ASCII newlines per WHATWG before parsing, so a payload
  passes ``assert_safe_outbound_url`` and the raw string still reaches the
  write. The fix has to be on the serialisation.

Every test drives the ROUTE. Asserting on the regex would prove the pattern
matches and prove nothing about this route reaching it; the mutation check is
deleting the guard from the write and watching these go red.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import (  # noqa: E402
    context as context_routes,
    projects as projects_store,
)

FORGED = "ANTHROPIC_BASE_URL=http://attacker.invalid"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        projects_store,
        "load_projects",
        lambda *_a, **_k: {"p1": {"path": str(tmp_path), "name": "p1"}},
    )
    monkeypatch.setattr(projects_store, "save_projects", lambda *_a, **_k: None)
    (tmp_path / ".tfactory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tfactory" / ".env").write_text("EXISTING_KEY=keep-me")
    return tmp_path


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(
        context_routes.project_router, prefix="/api/projects/{projectId}"
    )
    with TestClient(app) as c:
        yield c


def _patch(client: TestClient, **provider_config: Any) -> Any:
    return client.patch(
        "/api/projects/p1/env",
        json={"graphitiProviderConfig": provider_config},
    )


def _env(project: Path) -> dict[str, str]:
    """Parse the file the way agent_service parses it, not with dotenv."""
    path = project / ".tfactory" / ".env"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


@pytest.mark.parametrize("sep", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_a_line_separator_is_refused_and_forges_nothing(
    client: TestClient, project: Path, sep: str
) -> None:
    """Every terminator form is refused, and no forged variable appears."""
    r = _patch(client, ollamaEmbeddingModel=f"nomic{sep}{FORGED}")
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "line separator" in r.json()["error"]

    env = _env(project)
    assert "ANTHROPIC_BASE_URL" not in env, "a forged assignment reached the file"
    assert env.get("EXISTING_KEY") == "keep-me", (
        "a refused write must not disturb the file"
    )


def test_a_url_key_is_refused_too_despite_passing_the_ssrf_check(
    client: TestClient, project: Path
) -> None:
    """urlparse strips newlines, so the #1112 guard admits this payload."""
    payload = f"http://8.8.8.8:11434/\n{FORGED}"
    assert urlparse(payload).hostname == "8.8.8.8", (
        "if urlparse stops stripping newlines this test no longer covers the gap"
    )

    r = _patch(client, ollamaBaseUrl=payload)
    assert r.json()["success"] is False
    assert "ANTHROPIC_BASE_URL" not in _env(project)


def test_ordinary_values_still_write(client: TestClient, project: Path) -> None:
    """The guard must not refuse legitimate settings."""
    r = _patch(client, ollamaEmbeddingModel="nomic-embed-text")
    assert r.json()["success"] is True
    assert _env(project)["OLLAMA_EMBEDDING_MODEL"] == "nomic-embed-text"
    assert _env(project)["EXISTING_KEY"] == "keep-me"


# The two token checks below were merged into a single exit while fixing #1124
# (PLR0911). Nothing covered either message before, so the refactor would have
# been silent either way -- these pin the behaviour, not the shape.


def _patch_token(client: TestClient, **body: Any) -> Any:
    """Credential fields sit at the top level of the body, not under a provider."""
    return client.patch("/api/projects/p1/env", json=body)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("   ", "cannot be empty"),
        ("short", "must be at least 10 characters"),
    ],
    ids=["whitespace-only", "too-short"],
)
def test_a_rejected_token_keeps_its_own_message(
    client: TestClient, project: Path, value: str, expected: str
) -> None:
    r = _patch_token(client, githubToken=value)
    assert r.json()["success"] is False
    assert r.json()["error"] == f"githubToken {expected}"
    assert "GITHUB_TOKEN" not in _env(project)


def test_an_acceptable_token_is_written(client: TestClient, project: Path) -> None:
    r = _patch_token(client, githubToken="ghp_long_enough_token")
    assert r.json()["success"] is True
    assert _env(project)["GITHUB_TOKEN"] == "ghp_long_enough_token"

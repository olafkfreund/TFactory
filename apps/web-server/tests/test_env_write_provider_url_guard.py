"""PATCH /env cannot launder a provider base URL into .tfactory/.env (#1112).

The laundering route, end to end:

    PATCH /api/projects/{id}/env
        body.graphitiProviderConfig.ollamaBaseUrl
    -> routes/context.py writes OLLAMA_BASE_URL into <project>/.tfactory/.env
    -> services/agent_service.py loads that file into the agent subprocess env
    -> apps/backend providers/_ollama_http.py, providers/openai_compatible*.py
       and integrations/graphiti read it back and FETCH it, with _build_headers
       attaching the configured API key.

routes/settings_llm_providers.py already refuses a metadata-range Ollama URL on
the way in. This route accepted the same value for the same eventual fetch --
one door checked, one door open -- which is what "laundered" means here: the
value crosses a trust boundary by being written to a file, and the check the
API layer performs is simply not on this path.

Every test drives the ROUTE. Calling assert_safe_outbound_url directly would
prove the guard works and prove nothing about this route reaching it; the
mutation check in the PR is deleting the call and watching these go red.

8.8.8.8 is the public case because ipaddress reports it is_global. Not
203.0.113.x -- Python counts TEST-NET-3 as private, so it would be admitted
only by allow_private=True and would demonstrate nothing.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import (  # noqa: E402 - after the sys.path insert above
    context as context_routes,
    projects as projects_store,
)

METADATA = "http://169.254.169.254:11434"
PRIVATE = "http://10.0.0.5:11434"
LOOPBACK = "http://127.0.0.1:11434"
PUBLIC = "http://8.8.8.8:11434"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        projects_store,
        "load_projects",
        lambda *_a, **_k: {"p1": {"path": str(tmp_path), "name": "p1"}},
    )
    monkeypatch.setattr(projects_store, "save_projects", lambda *_a, **_k: None)
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
    path = project / ".tfactory" / ".env"
    if not path.exists():
        return {}
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and "=" in line
    )


def test_the_public_fixture_is_actually_public() -> None:
    assert ipaddress.ip_address("8.8.8.8").is_global
    assert not ipaddress.ip_address("10.0.0.5").is_global


def test_a_metadata_ollama_url_is_refused_and_never_written(
    client: TestClient, project: Path
) -> None:
    r = _patch(client, ollamaBaseUrl=METADATA)
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "ollamaBaseUrl" in r.json()["error"]
    assert "OLLAMA_BASE_URL" not in _env(project)


def test_a_metadata_azure_endpoint_is_refused_too(
    client: TestClient, project: Path
) -> None:
    """Same loop, same fetch chain -- guarding only the key #1112 named would
    leave the identical sibling open."""
    r = _patch(client, azureOpenaiBaseUrl="http://169.254.169.254/openai")
    assert r.json()["success"] is False
    assert "AZURE_OPENAI_ENDPOINT" not in _env(project)


@pytest.mark.parametrize("url", [LOOPBACK, PRIVATE])
def test_a_self_hosted_ollama_is_still_accepted(
    client: TestClient, project: Path, url: str
) -> None:
    """allow_private=True: localhost and a LAN box ARE the product here."""
    r = _patch(client, ollamaBaseUrl=url)
    assert r.json()["success"] is True
    assert _env(project)["OLLAMA_BASE_URL"] == url


def test_an_ordinary_public_url_is_still_accepted(
    client: TestClient, project: Path
) -> None:
    r = _patch(client, ollamaBaseUrl=PUBLIC)
    assert r.json()["success"] is True
    assert _env(project)["OLLAMA_BASE_URL"] == PUBLIC


def test_a_non_http_scheme_is_refused(client: TestClient, project: Path) -> None:
    r = _patch(client, ollamaBaseUrl="file:///etc/passwd")
    assert r.json()["success"] is False
    assert "OLLAMA_BASE_URL" not in _env(project)


def test_a_rejected_write_leaves_the_other_keys_alone(
    client: TestClient, project: Path
) -> None:
    """The refusal returns before ANY key is written, so a bad URL cannot be
    used to half-apply a settings patch."""
    r = _patch(client, ollamaEmbeddingModel="nomic", ollamaBaseUrl=METADATA)
    assert r.json()["success"] is False
    assert _env(project) == {}


def test_a_non_url_provider_field_is_untouched_by_the_guard(
    client: TestClient, project: Path
) -> None:
    r = _patch(client, ollamaEmbeddingModel="nomic-embed-text")
    assert r.json()["success"] is True
    assert _env(project)["OLLAMA_EMBEDDING_MODEL"] == "nomic-embed-text"

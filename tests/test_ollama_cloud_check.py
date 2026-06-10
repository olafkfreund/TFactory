#!/usr/bin/env python3
"""Unit tests for the Ollama Cloud connectivity check (issue #306).

Covers env/arg resolution, the OpenAI ``/v1/models`` body parser, and the
``check_ollama_cloud`` probe (success + HTTP error + unreachable). HTTP is
mocked via ``urllib.request.urlopen`` patches — no network, no key needed.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from providers.ollama_cloud_check import (  # noqa: E402
    DEFAULT_BASE_URL,
    _parse_model_ids,
    _resolve_api_key,
    _resolve_base_url,
    check_ollama_cloud,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_urlopen_response(payload: dict, status: int = 200) -> MagicMock:
    raw = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = MagicMock(
        return_value=MagicMock(
            read=MagicMock(return_value=raw),
            getcode=MagicMock(return_value=status),
        )
    )
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _http_error(code: int, reason: str = "Unauthorized") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://ollama.com/v1/models",
        code=code,
        msg=reason,
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# base_url / api_key resolution
# ---------------------------------------------------------------------------


def test_base_url_default(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_BASE_URL", raising=False)
    assert _resolve_base_url(None) == DEFAULT_BASE_URL


def test_base_url_strips_v1_suffix():
    assert _resolve_base_url("https://ollama.com/v1") == "https://ollama.com"
    assert _resolve_base_url("https://ollama.com/v1/") == "https://ollama.com"


def test_base_url_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://env.example")
    assert _resolve_base_url("https://arg.example") == "https://arg.example"


def test_base_url_env_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
    assert _resolve_base_url(None) == "https://ollama.com"


def test_api_key_precedence(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "compat-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    assert _resolve_api_key(None) == "compat-key"
    assert _resolve_api_key("arg-key") == "arg-key"


def test_api_key_ollama_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    assert _resolve_api_key(None) == "ollama-key"


def test_api_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert _resolve_api_key(None) is None


# ---------------------------------------------------------------------------
# model-id parser
# ---------------------------------------------------------------------------


def test_parse_model_ids_ok():
    body = json.dumps(
        {"data": [{"id": "gpt-oss:120b"}, {"id": "qwen3-coder:480b"}]}
    ).encode()
    assert _parse_model_ids(body) == ["gpt-oss:120b", "qwen3-coder:480b"]


def test_parse_model_ids_empty():
    assert _parse_model_ids(json.dumps({"data": []}).encode()) == []


def test_parse_model_ids_malformed():
    assert _parse_model_ids(b"not json") == []
    assert _parse_model_ids(json.dumps({"unexpected": 1}).encode()) == []


# ---------------------------------------------------------------------------
# check_ollama_cloud
# ---------------------------------------------------------------------------


def test_check_success():
    payload = {"data": [{"id": "gpt-oss:120b"}, {"id": "gemma3:27b"}]}
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_response(payload)):
        result = check_ollama_cloud("https://ollama.com", "sk-test")
    assert result.ok is True
    assert result.status == 200
    assert result.models == ["gpt-oss:120b", "gemma3:27b"]


def test_check_unauthorized():
    with patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
        result = check_ollama_cloud("https://ollama.com", None)
    assert result.ok is False
    assert result.status == 401
    assert "401" in result.error


def test_check_unreachable():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("name resolution failed"),
    ):
        result = check_ollama_cloud("https://ollama.com", "sk-test")
    assert result.ok is False
    assert "cannot reach" in result.error


def test_check_reachable_but_no_models():
    with patch("urllib.request.urlopen", return_value=_mock_urlopen_response({"data": []})):
        result = check_ollama_cloud("https://ollama.com", "sk-test")
    assert result.ok is False
    assert "no models" in result.error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

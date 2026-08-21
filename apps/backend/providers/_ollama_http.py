"""Shared synchronous Ollama HTTP helpers for the plain + agentic providers.

Both ``OllamaProvider`` and ``OllamaAgenticProvider`` need identical
synchronous request/health-check helpers (run via ``asyncio.to_thread``). They
live here as a mixin so the two providers can't drift.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

_PATH_TAGS: str = "/api/tags"

DEFAULT_OLLAMA_BASE_URL: str = "http://localhost:11434"

# Env vars naming the Ollama endpoint, in precedence order. ``OLLAMA_HOST`` is
# Ollama's own client variable and is accepted last so an operator who has set
# only that is not surprised by localhost.
_BASE_URL_ENV: tuple[str, ...] = ("OLLAMA_BASE_URL", "OLLAMA_API_URL", "OLLAMA_HOST")


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val and val.strip():
            return val.strip()
    return None


def resolve_ollama_base_url() -> str:
    """The Ollama endpoint from the environment, else localhost (#1099).

    THE resolver for the SELF-HOSTED Ollama endpoint, so a deployment that sets
    ``OLLAMA_BASE_URL`` cannot be honoured by one caller and ignored by another.

    That was the bug: this provider read no environment variable at all, so
    ``OllamaAgenticProvider`` defaulted to ``http://localhost:11434`` and a
    contract pinned to ``ollama:<model>`` silently tried localhost inside a pod,
    where there is no Ollama. The endpoint was only reachable by spelling the
    phase ``openai-compatible:`` instead.

    Ported from AIFactory, which fixed the same vendored provider first — the
    canonical moved and this fork did not, and nothing was red because the file
    is not byte-compared.

    NO cloud counterpart here, deliberately, and that is the one place TFactory
    differs from AIFactory: #870 routes ``ollama-cloud`` through
    ``openai-compatible`` (see ``agents/gen_functional._RUNTIME_TO_PROVIDER``),
    which carries its own ``base_url`` and ``api_key``. An
    ``OLLAMA_API_KEY``-reading resolver here would have no caller, and an unused
    credential path is worse than a missing one.
    """
    return (_first_env(_BASE_URL_ENV) or DEFAULT_OLLAMA_BASE_URL).rstrip("/")


class OllamaHTTPMixin:
    """Synchronous HTTP helpers shared by the Ollama providers.

    Both providers assign ``_base_url`` and ``_timeout`` in ``__init__``; they
    are declared here as annotations (no value) so the mixin's methods
    type-check under ``mypy --strict`` without creating real class attributes.
    """

    _base_url: str
    _timeout: int

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous HTTP POST to the Ollama API.

        This is intentionally synchronous so it can be run in a thread
        via ``asyncio.to_thread()`` without blocking the event loop.

        Args:
            url: Full URL to POST to (e.g. ``"http://localhost:11434/api/chat"``).
            payload: JSON-serialisable request body dict.

        Returns:
            Parsed JSON response as a Python dict.

        Raises:
            RuntimeError: On HTTP errors or JSON decode failures.
        """
        body_bytes = json.dumps(payload).encode("utf-8")
        # URL is built from the configured Ollama base_url (http/https), not
        # untrusted input — the urllib scheme audit (S310) does not apply.
        req = urllib.request.Request(  # noqa: S310
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            error_body = ""
            with contextlib.suppress(Exception):
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Ollama API HTTP error {exc.code}: {exc.reason}. Response body: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama server at '{self._base_url}': {exc.reason}. "
                "Ensure Ollama is running (ollama serve) and base_url is correct."
            ) from exc

        try:
            parsed: dict[str, Any] = json.loads(raw.decode("utf-8", errors="replace"))
            return parsed
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama API returned invalid JSON: {exc}") from exc

    def _verify_connection(self) -> None:
        """Synchronous health check — verify the Ollama server is reachable.

        Issues a lightweight GET to ``/api/tags`` (lists available models).
        Raises ``RuntimeError`` if the server cannot be reached.  Called
        from ``__aenter__`` via ``asyncio.to_thread()``.

        Raises:
            RuntimeError: If the server is unreachable or returns an error.
        """
        url = f"{self._base_url}{_PATH_TAGS}"
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                resp.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama server at '{self._base_url}': {exc.reason}. "
                "Ensure Ollama is running (ollama serve) and base_url is correct."
            ) from exc
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Ollama server health check failed — HTTP {exc.code}: {exc.reason}."
            ) from exc

"""Ollama Cloud connectivity check (issue #306).

Ollama Cloud is an **OpenAI-compatible** endpoint at ``https://ollama.com/v1``
that — unlike the local Ollama daemon — **requires a real API key** (minted at
https://ollama.com/settings/keys). TFactory reaches it through the existing
``openai-compatible`` provider, no new provider needed: set a task's model to
``openai-compatible:<model>`` (e.g. ``openai-compatible:qwen3-coder:480b``) with

    OPENAI_COMPATIBLE_BASE_URL=https://ollama.com
    OPENAI_COMPATIBLE_API_KEY=<your ollama key>

and ``phase_config.get_provider_extra_kwargs`` falls back to those env vars when
no per-user endpoint is saved (see the issue thread for the full resolution
path).

This module is a standalone *connectivity probe*: it GETs ``<base>/v1/models``
with the key and lists the available cloud models, so you can confirm the key +
network egress work before kicking off a task.

    python -m providers.ollama_cloud_check
    python apps/backend/providers/ollama_cloud_check.py --base-url https://ollama.com --api-key sk-...

Exit code is ``0`` only when the endpoint responds and at least one model is
listed; non-zero (with a one-line reason on stderr) otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://ollama.com"
_MODELS_PATH = "/v1/models"


@dataclass
class CheckResult:
    """Outcome of a connectivity probe."""

    ok: bool
    base_url: str
    models: list[str] = field(default_factory=list)
    status: int | None = None
    error: str | None = None


def _resolve_base_url(explicit: str | None) -> str:
    """Pick the base URL: explicit arg > env > default.

    Accepts the URL with or without a trailing ``/v1`` — it's normalised so
    ``/v1/models`` is appended exactly once. This mirrors the provider layer,
    which stores the base WITHOUT the ``/v1`` suffix and appends the path.
    """
    raw = (
        explicit
        or os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
        or os.environ.get("OLLAMA_CLOUD_BASE_URL", "").strip()
        or DEFAULT_BASE_URL
    )
    base = raw.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _resolve_api_key(explicit: str | None) -> str | None:
    """Pick the API key: explicit arg > OPENAI_COMPATIBLE_API_KEY > OLLAMA_API_KEY."""
    return (
        explicit
        or os.environ.get("OPENAI_COMPATIBLE_API_KEY", "").strip()
        or os.environ.get("OLLAMA_API_KEY", "").strip()
        or None
    )


def check_ollama_cloud(
    base_url: str,
    api_key: str | None,
    *,
    timeout: int = 10,
) -> CheckResult:
    """GET ``<base_url>/v1/models`` with the key and parse the model list.

    ``base_url`` is expected to be normalised (no ``/v1`` suffix). Network and
    HTTP errors are captured into the returned ``CheckResult`` rather than
    raised, so callers get a single structured outcome.
    """
    url = f"{base_url}{_MODELS_PATH}"
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code in (401, 403):
            hint = " — check OPENAI_COMPATIBLE_API_KEY / OLLAMA_API_KEY (cloud requires a key)"
        return CheckResult(
            ok=False,
            base_url=base_url,
            status=exc.code,
            error=f"HTTP {exc.code}: {exc.reason}{hint}",
        )
    except urllib.error.URLError as exc:
        return CheckResult(
            ok=False,
            base_url=base_url,
            error=f"cannot reach {url}: {exc.reason}",
        )

    models = _parse_model_ids(body)
    if not models:
        return CheckResult(
            ok=False,
            base_url=base_url,
            status=status,
            error="endpoint reachable but returned no models",
        )
    return CheckResult(ok=True, base_url=base_url, status=status, models=models)


def _parse_model_ids(body: bytes) -> list[str]:
    """Extract model ids from an OpenAI ``/v1/models`` response body."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for entry in data:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            ids.append(entry["id"])
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe Ollama Cloud (OpenAI-compatible) connectivity + list models.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL (default: $OPENAI_COMPATIBLE_BASE_URL / $OLLAMA_CLOUD_BASE_URL / "
        f"{DEFAULT_BASE_URL}). A trailing /v1 is tolerated.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (default: $OPENAI_COMPATIBLE_API_KEY / $OLLAMA_API_KEY).",
    )
    parser.add_argument("--timeout", type=int, default=10, help="Request timeout (s).")
    args = parser.parse_args(argv)

    base_url = _resolve_base_url(args.base_url)
    api_key = _resolve_api_key(args.api_key)

    if not api_key:
        print(
            "warning: no API key found — Ollama Cloud requires one "
            "(set OPENAI_COMPATIBLE_API_KEY or OLLAMA_API_KEY, or pass --api-key)",
            file=sys.stderr,
        )

    result = check_ollama_cloud(base_url, api_key, timeout=args.timeout)

    if result.ok:
        print(
            f"✅ Ollama Cloud reachable at {result.base_url}/v1 — {len(result.models)} model(s):"
        )
        for model_id in result.models:
            print(f"  • {model_id}")
        return 0

    print(
        f"❌ Ollama Cloud check failed ({result.base_url}/v1): {result.error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

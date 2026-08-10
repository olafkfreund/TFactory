"""Provider-credential pre-flight for the health endpoint (RFC-0008 §3.4, #109).

A truthful, quota-free check that each LLM provider's credential env var is set
and non-empty. It catches a *missing / un-rotated* token — the gap that silently
produced an empty build in the 2026-06-18 taskboard demo (a consumer that never
received the rotated CLAUDE_CODE_OAUTH_TOKEN). It deliberately does NOT validate
the token live (no quota burn, no false confidence): this is a CONFIGURATION
pre-flight, surfaced by CFactory as a provider-auth health tile.

#858: configuration alone could not distinguish a live credential from a revoked
one, and a revoked one took a whole verification run down on 2026-08-07 while
this endpoint reported healthy. The anthropic entry is therefore enriched with a
live ``validity`` from ``credential_validity`` — a quota-free authenticated call,
cached and run off the request path. ``configured`` keeps its original meaning;
``validity`` is the separate, additive field.
"""

from __future__ import annotations

import os
from typing import Any

# Provider -> the env vars that, if any is set+non-empty, mean the credential is
# configured for that provider. Mirrors the factory secret set.
_PROVIDER_ENVS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anthropic", ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")),
    ("gemini", ("GEMINI_API_KEY",)),
    ("openai", ("OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY")),
    ("ollama", ("OLLAMA_API_KEY", "OLLAMA_API_URL", "OLLAMA_BASE_URL")),
)


def provider_credential_health(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return providers + ``any_configured`` + ``any_invalid`` (#109, #858).

    ``env`` is injectable for tests; defaults to ``os.environ``. ``configured``
    is True when at least one of the provider's credential env vars is present
    and non-empty (whitespace-stripped). ``any_configured`` is False only when
    the service has NO usable provider credential at all — the high-confidence
    "this service cannot authenticate to any model" alert.

    The anthropic entry additionally carries ``validity`` /
    ``validity_detail`` / ``validity_checked_at`` from a live, quota-free
    authenticated call (#858). ``validity`` is one of ``valid`` / ``invalid`` /
    ``unknown``; ``unknown`` means the check could not reach a verdict and is
    never collapsed into either neighbour.

    ``any_invalid`` is the loud roll-up: True only when a live check DEFINITIVELY
    rejected a credential. It is deliberately not derived from ``unknown``, so a
    network blip cannot manufacture an outage alert.
    """
    source = os.environ if env is None else env
    providers: list[dict[str, Any]] = [
        {
            "name": name,
            "configured": any(bool((source.get(e) or "").strip()) for e in envs),
        }
        for name, envs in _PROVIDER_ENVS
    ]

    # Best-effort: a probe failure must never break the health endpoint, which
    # is also the liveness probe.
    try:
        from .credential_validity import credential_validity  # noqa: PLC0415

        verdict = credential_validity()
    except Exception as exc:  # noqa: BLE001
        verdict = {
            "validity": "unknown",
            "detail": f"validity check unavailable ({type(exc).__name__})",
            "checked_at": None,
        }
    for provider in providers:
        if provider["name"] == "anthropic":
            provider["validity"] = verdict["validity"]
            provider["validity_detail"] = verdict["detail"]
            provider["validity_checked_at"] = verdict["checked_at"]

    return {
        "providers": providers,
        "any_configured": any(p["configured"] for p in providers),
        "any_invalid": any(p.get("validity") == "invalid" for p in providers),
    }

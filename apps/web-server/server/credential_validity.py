"""Live Claude-credential validity for the health endpoint (#858).

``provider_health`` answers "is a token CONFIGURED". This module answers the
question that actually took a verification run down on 2026-08-07: "does the
token still AUTHENTICATE". Both were reported as ``healthy`` throughout, and the
only symptom was a misattributed ``planner_invalid_missing_after_retry`` — the
real cause (``401 OAuth access token has been revoked``) was buried in a phase
log. Presence is not validity.

Factory#642's test for this class of defect is: *if this had done nothing at all,
would the output look different?* A presence check fails that test — an unset
token and a revoked token produce the same "configured" line. So this module
reads the ARTEFACT: one authenticated ``GET /v1/models`` against the credential
the runtime would actually use. It costs no model tokens.

Three states, never two
-----------------------
``valid`` / ``invalid`` / ``unknown``. The third is load-bearing and must not be
collapsed into either neighbour — reporting "could not check" as "invalid" is a
false alarm that gets muted, and reporting it as "valid" is the original defect
wearing a new hat. A network error, an absent token, and an unexpected status are
all ``unknown`` with a reason, never a verdict. (Same distinction PFactory's
readiness gate draws with ``not_applicable`` vs ``pass``.)

Refresh-token nuance
--------------------
``core.auth.prefer_refreshable_credentials`` means the SDK owns auth whenever
``~/.claude/.credentials.json`` carries a ``refreshToken``: it renews an expired
ACCESS token by itself. So a rejected access token is only a genuine outage when
the SDK has no usable refresh path. When a refresh token is present and its own
``refreshTokenExpiresAt`` is still in the future, a rejected access token is
reported ``unknown`` ("the SDK is expected to renew it"), not ``invalid`` — a
permanently-red signal is an ignored signal. When there is no usable refresh
path, the same rejection is ``invalid``, which is exactly the 2026-08-07 shape
(access token expired 2026-06-07, refresh revoked).

Not a duplicate of Factory#628
------------------------------
That fixed ``cred-sync`` clobbering a good credential with a stale seed — which
file wins. This checks whether the winner actually works. The two are
complementary: #628 stops the bad write, this notices when one happened anyway.

Never blocks the event loop
---------------------------
``/api/health`` is BOTH the liveness and the readiness probe (charts/tfactory/
templates/deployment.yaml), so this module must never make the handler slow or
change its status code — a hung socket inside the handler would fail liveness and
crashloop the pod over a credential question. The handler therefore only ever
reads a cached verdict; a stale cache schedules a refresh on a worker thread and
returns the previous answer (``unknown`` on the very first call).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Validity = Literal["valid", "invalid", "unknown"]
# `_probe` reports what the API said; `_classify` turns that into a Validity.
# Separate types so "rejected" can never leak out as a public verdict.
_ProbeOutcome = Literal["valid", "rejected", "unknown"]

# One cheap authenticated call. GET /v1/models is a real authenticated endpoint
# that consumes no model tokens and no quota; `limit=1` keeps the body tiny.
_MODELS_URL = "https://api.anthropic.com/v1/models?limit=1"
_ANTHROPIC_VERSION = "2023-06-01"
# OAuth tokens authenticate via `Authorization: Bearer` plus this beta header
# (NOT `x-api-key`). Harmless on the endpoints that don't require it.
_OAUTH_BETA = "oauth-2025-04-20"
_HTTP_OK = 200
_REJECTED_STATUSES = (401, 403)

_TTL_ENV = "APP_CREDENTIAL_CHECK_TTL_SECONDS"
_DEFAULT_TTL_S = 300.0
_TIMEOUT_ENV = "APP_CREDENTIAL_CHECK_TIMEOUT_SECONDS"
_DEFAULT_TIMEOUT_S = 10.0
_ENABLED_ENV = "APP_CREDENTIAL_CHECK_ENABLED"

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
# Mirrors core.auth.AUTH_TOKEN_ENV_VARS. ANTHROPIC_API_KEY is deliberately absent
# there (no silent API billing) and so is absent here — checking a token the
# runtime would never use would report on the wrong artefact.
_TOKEN_ENV_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")


# ponytail: one mutable holder, not a cache class. There is exactly one
# credential and one process; per-key eviction would be scaffolding. Attributes
# rather than module globals so nothing needs a `global` statement.
@dataclass
class _Cache:
    validity: Validity = "unknown"
    detail: str = "not checked yet"
    checked_at: str | None = None
    monotonic: float | None = None
    running: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "validity": self.validity,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


_lock = threading.Lock()
_cache = _Cache()


def _ttl_s() -> float:
    """TTL, read per call so tests (and an operator) can change it live."""
    try:
        return float(os.environ.get(_TTL_ENV, "") or _DEFAULT_TTL_S)
    except ValueError:
        return _DEFAULT_TTL_S


def _timeout_s() -> float:
    try:
        return float(os.environ.get(_TIMEOUT_ENV, "") or _DEFAULT_TIMEOUT_S)
    except ValueError:
        return _DEFAULT_TIMEOUT_S


def _enabled() -> bool:
    """Default ON. Set APP_CREDENTIAL_CHECK_ENABLED=0 to disable the probe."""
    return (os.environ.get(_ENABLED_ENV, "1") or "1").strip() != "0"


def _read_credentials_file() -> dict[str, Any]:
    """The ``claudeAiOauth`` block from the SDK's credentials file, or ``{}``."""
    try:
        data = json.loads(_CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    oauth = data.get("claudeAiOauth", data)
    return oauth if isinstance(oauth, dict) else {}


def _has_live_refresh_path(creds: dict[str, Any]) -> bool:
    """True when the SDK can renew an expired access token by itself.

    A ``refreshToken`` alone is not enough: the 2026-08-07 outage had one, and it
    was revoked. ``refreshTokenExpiresAt`` (when present) is the honest bound —
    past it, there is provably no refresh path. Absent, we can only say a refresh
    token exists, which is enough to keep a rejected access token out of the
    ``invalid`` bucket but is reported as such in the detail.
    """
    if not creds.get("refreshToken"):
        return False
    expires_at = creds.get("refreshTokenExpiresAt")
    if expires_at is None:
        return True
    try:
        return int(expires_at) / 1000.0 > time.time()
    except (TypeError, ValueError):
        return True


def _resolve_token() -> tuple[str | None, str, dict[str, Any]]:
    """Return ``(token, source, credentials_block)`` for the token the SDK uses.

    Resolution mirrors ``core.auth`` INCLUDING ``prefer_refreshable_credentials``:
    when the credentials file carries a refresh token the SDK owns auth and the
    static env token is scrubbed at spawn time, so the file's access token — not
    the env var — is what a task will actually present. Probing the env token
    there would check a credential nothing uses.
    """
    creds = _read_credentials_file()
    file_token = creds.get("accessToken") or None
    if file_token and creds.get("refreshToken"):
        return file_token, "credentials-file", creds
    for var in _TOKEN_ENV_VARS:
        token = (os.environ.get(var) or "").strip()
        if token:
            return token, var, creds
    if file_token:
        return file_token, "credentials-file", creds
    return None, "none", creds


def _probe(token: str) -> tuple[_ProbeOutcome, str]:
    """One authenticated request. Returns ``(validity, detail)``; never raises.

    HTTP 200 is the only positive. 401/403 mean the token was REJECTED — the
    caller decides whether that is ``invalid`` or a renewable ``unknown``.
    Everything else (transport error, 5xx, 429) is ``unknown``: the API's health
    is not the credential's validity, and conflating them manufactures alarms.
    """
    request = urllib.request.Request(  # noqa: S310 - constant https URL
        _MODELS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-version": _ANTHROPIC_VERSION,
            "anthropic-beta": _OAUTH_BETA,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_s()) as response:  # noqa: S310
            if response.status == _HTTP_OK:
                return "valid", "authenticated against /v1/models"
            return "unknown", f"unexpected status {response.status}"
    except urllib.error.HTTPError as exc:
        if exc.code in _REJECTED_STATUSES:
            # The message is the API's own and carries no credential value.
            return "rejected", f"HTTP {exc.code} from /v1/models"
        return "unknown", f"HTTP {exc.code} from /v1/models"
    except Exception as exc:  # noqa: BLE001 - a probe must never break health
        return "unknown", f"could not reach the API ({type(exc).__name__})"


def _classify() -> tuple[Validity, str]:
    """Resolve the token, probe it, and map the result onto the three states."""
    token, source, creds = _resolve_token()
    if not token:
        return "unknown", "no Claude credential configured"

    outcome, detail = _probe(token)
    if outcome == "valid":
        return "valid", f"{detail} (source: {source})"
    if outcome != "rejected":
        return "unknown", f"{detail} (source: {source})"

    # Rejected. Whether that is an outage depends on the SDK's refresh path.
    if _has_live_refresh_path(creds):
        bound = (
            "unexpired"
            if creds.get("refreshTokenExpiresAt")
            else "present but with no stated expiry"
        )
        return (
            "unknown",
            f"access token rejected ({detail}); a refresh token is {bound}, so the "
            "SDK is expected to renew it — validity not confirmed",
        )
    return (
        "invalid",
        f"access token rejected ({detail}) and no usable refresh path "
        f"(source: {source}) — tasks using this credential will fail to authenticate",
    )


def _refresh() -> None:
    """Run one classification and publish it. Called on a worker thread."""
    try:
        validity, detail = _classify()
    except Exception as exc:  # noqa: BLE001 - never let the probe thread die loud
        validity, detail = "unknown", f"probe error ({type(exc).__name__})"
    with _lock:
        previous = _cache.validity
        _cache.validity = validity
        _cache.detail = detail
        _cache.checked_at = datetime.now(UTC).isoformat()
        _cache.monotonic = time.monotonic()
        _cache.running = False
    # Loud on TRANSITION, not once per request — a line every 5s is a line
    # nobody reads, which is how the 2026-08-07 failure stayed invisible.
    if validity != previous:
        log = logger.error if validity == "invalid" else logger.info
        log("claude credential validity %s -> %s: %s", previous, validity, detail)


def _maybe_schedule_refresh() -> None:
    """Kick a background probe when the cached verdict is stale. Single-flight."""
    with _lock:
        if _cache.running:
            return
        fresh = (
            _cache.monotonic is not None
            and (time.monotonic() - _cache.monotonic) < _ttl_s()
        )
        if fresh:
            return
        _cache.running = True
    threading.Thread(target=_refresh, name="credential-validity", daemon=True).start()


def credential_validity(*, block: bool = False) -> dict[str, Any]:
    """Return ``{validity, detail, checked_at}`` for the Claude credential.

    Reads the cache and schedules a background refresh when stale, so the caller
    (the k8s liveness+readiness probe) never waits on the network. ``block=True``
    runs the probe inline — for tests and one-shot CLI use only, never a request.
    """
    if not _enabled():
        return {
            "validity": "unknown",
            "detail": f"disabled via {_ENABLED_ENV}=0",
            "checked_at": None,
        }
    if block:
        _refresh()
    else:
        _maybe_schedule_refresh()
    with _lock:
        return _cache.snapshot()


def reset_cache() -> None:
    """Drop the cached verdict. Test hook."""
    with _lock:
        _cache.validity = "unknown"
        _cache.detail = "not checked yet"
        _cache.checked_at = None
        _cache.monotonic = None
        _cache.running = False


if __name__ == "__main__":  # pragma: no cover - operator one-shot
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(credential_validity(block=True), indent=2))  # noqa: T201

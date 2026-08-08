"""Live Claude-credential validity for the health endpoint (#858).

The defect: `/health` could not distinguish a live credential from a revoked one,
because it only checked that a token was PRESENT. On 2026-08-07 a revoked token
took a verification run down while the endpoint reported healthy throughout.

Factory#642's test for the class — "if this had done nothing at all, would the
output look different?" — is what these tests encode: a REJECTED token must
produce a different payload from an ACCEPTED one, and neither may be confused
with "could not check".
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

_WS = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

from server import credential_validity as cv  # noqa: E402
from server.provider_health import provider_credential_health  # noqa: E402

_TOKEN = "sk-ant-oat01-" + "x" * 60  # noqa: S105 - fake, never a real credential
_FUTURE_MS = 4_102_444_800_000  # 2100-01-01
_PAST_MS = 1_749_254_400_000  # 2025-06-07


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch):
    cv.reset_cache()
    monkeypatch.delenv(cv._ENABLED_ENV, raising=False)
    for var in cv._TOKEN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    cv.reset_cache()


def _write_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **fields) -> None:
    """Point the module at a scratch ~/.claude/.credentials.json."""
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": fields}))
    monkeypatch.setattr(cv, "_CREDENTIALS_PATH", path)


def _http_error(code: int):
    return urllib.error.HTTPError(cv._MODELS_URL, code, "nope", {}, None)  # type: ignore[arg-type]


class _Resp:
    def __init__(self, status: int):
        self.status = status

    def read(self) -> bytes:
        return b'{"data":[]}'

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _urlopen(result):
    """Build a urlopen stand-in that returns a response or raises."""

    def _fn(request, timeout=None):
        if isinstance(result, Exception):
            raise result
        return result

    return _fn


# ── the artefact check: accepted vs rejected must differ ──────────────────────


def test_live_token_reports_valid(tmp_path, monkeypatch):
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_Resp(200)))

    out = cv.credential_validity(block=True)

    assert out["validity"] == "valid"
    assert out["checked_at"] is not None


def test_revoked_token_with_no_refresh_path_reports_invalid(tmp_path, monkeypatch):
    # The 2026-08-07 shape: access token rejected AND no usable refresh path.
    _write_creds(
        tmp_path,
        monkeypatch,
        accessToken=_TOKEN,
        refreshToken="r",
        refreshTokenExpiresAt=_PAST_MS,
    )
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_http_error(401)))

    out = cv.credential_validity(block=True)

    assert out["validity"] == "invalid"
    assert "401" in out["detail"]


def test_accepted_and_rejected_payloads_differ(tmp_path, monkeypatch):
    """Factory#642: a check that cannot tell these apart is not evidence."""
    _write_creds(
        tmp_path,
        monkeypatch,
        accessToken=_TOKEN,
        refreshToken="r",
        refreshTokenExpiresAt=_PAST_MS,
    )
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_Resp(200)))
    good = cv.credential_validity(block=True)

    cv.reset_cache()
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_http_error(401)))
    bad = cv.credential_validity(block=True)

    assert good["validity"] != bad["validity"]


# ── the third state must never be collapsed ──────────────────────────────────


def test_network_failure_is_unknown_not_invalid(tmp_path, monkeypatch):
    # A blip must not manufacture an outage alert — that is the same defect
    # inverted, and a false-alarming signal gets muted.
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setattr(
        cv.urllib.request, "urlopen", _urlopen(OSError("name resolution failed"))
    )

    out = cv.credential_validity(block=True)

    assert out["validity"] == "unknown"
    assert "could not reach" in out["detail"]


def test_server_error_is_unknown_not_invalid(tmp_path, monkeypatch):
    # The API's health is not the credential's validity.
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_http_error(529)))

    out = cv.credential_validity(block=True)

    assert out["validity"] == "unknown"


def test_no_credential_is_unknown_not_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_CREDENTIALS_PATH", tmp_path / "absent.json")

    out = cv.credential_validity(block=True)

    assert out["validity"] == "unknown"
    assert "no Claude credential" in out["detail"]


def test_rejected_but_refreshable_is_unknown_not_invalid(tmp_path, monkeypatch):
    """A stale-but-renewable access token is the normal steady state.

    core.auth.prefer_refreshable_credentials lets the SDK renew it, so calling
    this `invalid` would leave /health permanently red — and a permanently-red
    signal is an ignored one.
    """
    _write_creds(
        tmp_path,
        monkeypatch,
        accessToken=_TOKEN,
        refreshToken="r",
        refreshTokenExpiresAt=_FUTURE_MS,
    )
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_http_error(401)))

    out = cv.credential_validity(block=True)

    assert out["validity"] == "unknown"
    assert "refresh token" in out["detail"]


# ── token resolution matches what the runtime actually uses ──────────────────


def test_refreshable_file_token_wins_over_env(tmp_path, monkeypatch):
    # prefer_refreshable_credentials scrubs the static env token at spawn time,
    # so the file's access token is what a task presents. Probing the env token
    # would report on a credential nothing uses.
    _write_creds(tmp_path, monkeypatch, accessToken="file-token", refreshToken="r")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")

    token, source, _ = cv._resolve_token()

    assert token == "file-token"
    assert source == "credentials-file"


def test_env_token_used_when_file_has_no_refresh_token(tmp_path, monkeypatch):
    _write_creds(tmp_path, monkeypatch, accessToken="file-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token")

    token, source, _ = cv._resolve_token()

    assert token == "env-token"
    assert source == "CLAUDE_CODE_OAUTH_TOKEN"


# ── the request is shaped the way OAuth tokens require ───────────────────────


def test_probe_sends_bearer_and_oauth_beta_header(tmp_path, monkeypatch):
    # OAuth tokens authenticate on `Authorization: Bearer` plus the oauth beta
    # header, NOT on `x-api-key`. Getting this wrong would 401 a good token and
    # report a healthy service as broken.
    seen: dict = {}

    def _capture(request, timeout=None):
        seen["headers"] = dict(request.headers)
        seen["url"] = request.full_url
        return _Resp(200)

    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setattr(cv.urllib.request, "urlopen", _capture)

    cv.credential_validity(block=True)

    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers["authorization"] == f"Bearer {_TOKEN}"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in headers
    assert seen["url"].startswith("https://api.anthropic.com/v1/models")


def test_no_credential_value_appears_in_the_payload(tmp_path, monkeypatch):
    # SECURITY: the health payload is unauthenticated (PUBLIC_PATHS) — no part
    # of a credential may ever reach it, on any code path.
    _write_creds(
        tmp_path,
        monkeypatch,
        accessToken=_TOKEN,
        refreshToken="secret-refresh",
        refreshTokenExpiresAt=_PAST_MS,
    )
    for result in (_Resp(200), _http_error(401), OSError("boom")):
        cv.reset_cache()
        monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(result))
        blob = json.dumps(cv.credential_validity(block=True))
        assert _TOKEN not in blob
        assert "secret-refresh" not in blob


# ── the health endpoint must stay fast and 200 ───────────────────────────────


def test_health_never_probes_on_the_request_path(tmp_path, monkeypatch):
    """/api/health is BOTH the liveness and readiness probe.

    A blocking network call in the handler would fail liveness on a hung socket
    and crashloop the pod over a credential question. The non-blocking read must
    therefore never call urlopen itself.
    """
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")

    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("health must not make a network call inline")

    monkeypatch.setattr(cv.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(cv.threading, "Thread", lambda **kw: _NoopThread())

    out = cv.credential_validity()

    assert out["validity"] == "unknown"


class _NoopThread:
    def start(self) -> None:
        return None


def test_cached_verdict_is_reused_within_the_ttl(tmp_path, monkeypatch):
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    calls = {"n": 0}

    def _count(request, timeout=None):
        calls["n"] += 1
        return _Resp(200)

    monkeypatch.setattr(cv.urllib.request, "urlopen", _count)
    cv.credential_validity(block=True)
    # A stale-cache refresh would spawn a thread; within the TTL it must not.
    monkeypatch.setattr(cv.threading, "Thread", lambda **kw: _NoopThread())
    for _ in range(5):
        cv.credential_validity()

    assert calls["n"] == 1


def test_probe_can_be_disabled(tmp_path, monkeypatch):
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setenv(cv._ENABLED_ENV, "0")

    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("probe must not run when disabled")

    monkeypatch.setattr(cv.urllib.request, "urlopen", _boom)

    out = cv.credential_validity(block=True)

    assert out["validity"] == "unknown"
    assert "disabled" in out["detail"]


# ── the health payload surfaces it ───────────────────────────────────────────


def test_health_payload_carries_validity_and_any_invalid(tmp_path, monkeypatch):
    _write_creds(
        tmp_path,
        monkeypatch,
        accessToken=_TOKEN,
        refreshToken="r",
        refreshTokenExpiresAt=_PAST_MS,
    )
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(_http_error(401)))
    cv.credential_validity(block=True)

    health = provider_credential_health(env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"})

    anthropic = next(p for p in health["providers"] if p["name"] == "anthropic")
    # The original meaning of `configured` is preserved; `validity` is additive
    # and is what distinguishes a present token from a working one.
    assert anthropic["configured"] is True
    assert anthropic["validity"] == "invalid"
    assert health["any_invalid"] is True


def test_any_invalid_is_false_when_validity_is_unknown(tmp_path, monkeypatch):
    # `unknown` must not raise the alarm — only a definite rejection does.
    _write_creds(tmp_path, monkeypatch, accessToken=_TOKEN, refreshToken="r")
    monkeypatch.setattr(cv.urllib.request, "urlopen", _urlopen(OSError("blip")))
    cv.credential_validity(block=True)

    health = provider_credential_health(env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"})

    anthropic = next(p for p in health["providers"] if p["name"] == "anthropic")
    assert anthropic["validity"] == "unknown"
    assert health["any_invalid"] is False

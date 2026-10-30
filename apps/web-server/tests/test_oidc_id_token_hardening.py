"""ID token validation through the configured Authlib Starlette client."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from authlib.jose import JsonWebKey, jwt
from joserfc.errors import (
    InvalidClaimError,
    MissingClaimError,
    UnsupportedAlgorithmError,
)
from server.oidc.client import (
    authorize_access_token,
    get_oauth_client,
    reset_oauth_client_cache,
)


def _authorize(monkeypatch, claims=None, *, alg="RS256", discovery_issuer=None):
    issuer = "https://issuer.example/realms/test"
    client_id = "test-client"
    nonce = "test-nonce"
    now = int(time.time())
    monkeypatch.setenv("APP_OIDC_ISSUER_URL", issuer)
    monkeypatch.setenv("APP_OIDC_CLIENT_ID", client_id)
    monkeypatch.setenv("APP_OIDC_CLIENT_SECRET", "test-secret")
    reset_oauth_client_cache()
    oidc = get_oauth_client().oidc

    rsa_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    keys = [rsa_key.as_dict(is_private=False)]
    signing_key = rsa_key
    if alg == "HS256":
        signing_key = JsonWebKey.generate_key("oct", 256, is_private=True)
        keys.append(signing_key.as_dict())
    oidc.server_metadata = {
        "_loaded_at": time.time(),
        "issuer": discovery_issuer or issuer,
        "jwks": {"keys": keys},
        "id_token_signing_alg_values_supported": ["RS256", "HS256"],
    }
    payload = {
        "iss": issuer,
        "aud": client_id,
        "exp": now + 3600,
        "iat": now,
        "sub": "user-123",
        "nonce": nonce,
    }
    if claims:
        payload.update(claims)
    if claims is not None and "exp" in claims and claims["exp"] is None:
        payload.pop("exp")
    signed = jwt.encode(
        {"alg": alg, "kid": signing_key.as_dict()["kid"]}, payload, signing_key
    )

    async def fetch_access_token(**_kwargs):
        return {"access_token": "access-token", "id_token": signed}

    monkeypatch.setattr(oidc, "fetch_access_token", fetch_access_token)
    request = SimpleNamespace(
        scope={"method": "GET"},
        query_params={"code": "test-code", "state": "test-state"},
        session={},
    )

    async def run():
        await oidc.framework.set_state_data(
            request.session, "test-state", {"nonce": nonce}
        )
        return await authorize_access_token(oidc, request)

    return asyncio.run(run())


def test_valid_rs256_id_token_is_accepted(monkeypatch):
    token = _authorize(monkeypatch)
    assert token["userinfo"]["sub"] == "user-123"


def test_discovery_cannot_override_configured_issuer(monkeypatch):
    wrong_issuer = "https://other.example/realms/test"
    with pytest.raises(InvalidClaimError):
        _authorize(monkeypatch, {"iss": wrong_issuer}, discovery_issuer=wrong_issuer)


def test_missing_exp_is_rejected(monkeypatch):
    with pytest.raises(MissingClaimError):
        _authorize(monkeypatch, {"exp": None})


def test_wrong_audience_is_rejected(monkeypatch):
    with pytest.raises(InvalidClaimError):
        _authorize(monkeypatch, {"aud": "other-client"})


def test_discovery_cannot_enable_hs256(monkeypatch):
    with pytest.raises(UnsupportedAlgorithmError):
        _authorize(monkeypatch, alg="HS256")

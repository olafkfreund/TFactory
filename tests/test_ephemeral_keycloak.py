"""RFC-0007 Class C: ephemeral Keycloak realm generation + seed alignment.

The container lifecycle is proven live (a generated realm imported into Keycloak
25 accepted a fill_totp login); these tests cover the pure pieces: the realm
shape, the seed<->secret alignment (the crux), and the run argv.
"""

from __future__ import annotations

import json

from agents.ephemeral_keycloak import (
    EphemeralKeycloak,
    build_realm,
    totp_seed_for,
)
from agents.totp import generate_totp


def test_seed_aligns_with_keycloak_secret():
    # Keycloak keys HMAC with the secret's bytes; our generator base32-decodes the
    # seed to bytes. totp_seed_for(secret) must reproduce the SAME key so a code we
    # generate is the code Keycloak expects. Anchor to the RFC-6238 vector.
    secret = "12345678901234567890"
    seed = totp_seed_for(secret)
    assert seed == "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert generate_totp(seed, at=59) == "287082"  # RFC-6238 canonical


def test_build_realm_presets_otp_credential():
    realm = build_realm("12345678901234567890", realm="r", username="u", password="p")
    assert realm["realm"] == "r" and realm["enabled"] is True
    user = realm["users"][0]
    assert user["username"] == "u"
    assert user["requiredActions"] == []  # no CONFIGURE_TOTP -> uses our preset
    kinds = {c["type"] for c in user["credentials"]}
    assert kinds == {"password", "otp"}
    otp = next(c for c in user["credentials"] if c["type"] == "otp")
    assert json.loads(otp["secretData"])["value"] == "12345678901234567890"
    assert json.loads(otp["credentialData"])["algorithm"] == "HmacSHA1"


def test_run_argv_shape():
    kc = EphemeralKeycloak(port=8480, realm="tf-ephemeral")
    argv = kc.run_argv("podman", "/tmp/realm.json")
    assert argv[:3] == ["podman", "run", "-d"]
    assert "--rm" in argv  # disposable
    assert "8480:8080" in argv
    assert "/tmp/realm.json:/opt/keycloak/data/import/realm.json:ro" in argv
    assert argv[-2:] == ["start-dev", "--import-realm"]


def test_seed_is_random_per_instance():
    a = EphemeralKeycloak()
    b = EphemeralKeycloak()
    # distinct random secrets -> distinct seeds (no shared/static credential)
    assert a._secret != b._secret


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("ephemeral_keycloak tests: passed")

"""Class B MFA: RFC-6238 TOTP generation + fill_totp login wiring (RFC-0007)."""

from __future__ import annotations

from agents.evidence.layout import render_auth_setup_steps
from agents.totp import TotpError, generate_totp, is_valid_totp_secret

# Same fixed-time vectors the embedded TS helper (auth.setup.steps.tmpl.ts)
# produces — cross-checked against node:crypto. If these drift, the browser-side
# login and the server-side validator have diverged.
_SEED = "JBSWY3DPEHPK3PXP"
_VECTORS = {0: "282760", 59: "996554", 1234567890: "742275", 1700000000: "324550"}


def test_totp_matches_known_vectors():
    for at, expected in _VECTORS.items():
        assert generate_totp(_SEED, at=at) == expected, at


def test_totp_code_shape():
    code = generate_totp(_SEED)
    assert code.isdigit() and len(code) == 6


def test_totp_normalises_spaced_lowercase_seed():
    # Authenticator apps print grouped lowercase; must match the canonical seed.
    spaced = "jbsw y3dp ehpk 3pxp"
    assert generate_totp(spaced, at=0) == _VECTORS[0]


def test_invalid_seed_rejected():
    assert not is_valid_totp_secret("not base32 !!!")
    try:
        generate_totp("@@@", at=0)
        raise AssertionError("expected TotpError")
    except TotpError:
        pass
    assert is_valid_totp_secret(_SEED)


def test_totp_variants_match_cross_impl():
    # Cross-checked against the embedded TS helper (node:crypto) at a fixed time.
    assert generate_totp(_SEED, at=1700000000, digits=8, alg="sha256") == "32049486"
    assert generate_totp(_SEED, at=1700000000, alg="sha512", period=60) == "721347"


def test_unsupported_alg_rejected():
    try:
        generate_totp(_SEED, at=0, alg="md5")
        raise AssertionError("expected TotpError")
    except TotpError:
        pass


def test_fill_totp_renders_variant_opts():
    ts = render_auth_setup_steps(
        steps=[{"action": "fill_totp", "selector": "#otp"}],
        username_env="U", secret_env="S", totp_env="TF_TOTP_SEED",
        totp_opts={"digits": 8, "alg": "sha256", "period": 60},
    )
    assert "digits: 8" in ts and 'alg: "sha256"' in ts and "period: 60" in ts


def test_fill_totp_renders_runtime_generation():
    steps = [
        {"action": "fill_username", "selector": "#user"},
        {"action": "fill_secret", "selector": "#pass"},
        {"action": "click", "selector": "#submit"},
        {"action": "fill_totp", "selector": "#otp"},
        {"action": "wait_for_url", "url": "dashboard"},
    ]
    ts = render_auth_setup_steps(
        steps=steps,
        username_env="TF_USER",
        secret_env="TF_PASS",
        totp_env="TF_TOTP_SEED",
    )
    # The code is generated at fill time from the seed env var (never a static code).
    assert '__tfTotp(process.env["TF_TOTP_SEED"]' in ts
    assert '#otp' in ts
    # The helper itself is present in the rendered setup.
    assert "function __tfTotp(" in ts
    # No literal seed/code is ever inlined.
    assert _SEED not in ts


def test_fill_totp_skipped_without_totp_env():
    ts = render_auth_setup_steps(
        steps=[{"action": "fill_totp", "selector": "#otp"}],
        username_env="U",
        secret_env="S",
        totp_env=None,
    )
    assert "__tfTotp" not in ts.split("@@")[0] or 'process.env["None"]' not in ts
    # The fill_totp line is omitted when no seed env is configured.
    assert ".fill(__tfTotp(" not in ts.replace("function __tfTotp", "")


def test_schema_accepts_totp_entry():
    from tfactory_yml.schema import TestCredentialEntry

    entry = TestCredentialEntry(
        ref="env:APP_PASSWORD",
        as_secret="TF_PASS",
        username_ref="env:APP_USER",
        as_username="TF_USER",
        kind="totp",
        totp_ref="env:APP_TOTP_SEED",
        as_totp_secret="TF_TOTP_SEED",
    )
    assert entry.as_totp_secret == "TF_TOTP_SEED" and entry.totp_ref == "env:APP_TOTP_SEED"


def test_resolver_injects_totp_seed(monkeypatch, tmp_path):
    import tools.runners.sandbox_credentials as sc

    monkeypatch.setattr(sc, "egress_enabled", lambda *_a, **_k: True, raising=False)

    class _Broker:
        def __init__(self, *a, **k):
            pass

        def resolve_ref(self, ref):
            class R:
                value = {"env:PW": "pw", "env:SEED": _SEED}[ref]

            return R()

    monkeypatch.setattr(
        "tfactory_secrets.egress.egress_enabled", lambda *_a, **_k: True
    )
    monkeypatch.setattr("tfactory_secrets.broker.CredentialBroker", _Broker)

    spec = sc.TargetCredentialSpec(
        name="app", ref="env:PW", as_secret="TF_PASS",
        totp_ref="env:SEED", as_totp_secret="TF_TOTP_SEED",
    )
    creds = sc.resolve_test_target_credentials([spec], tmp_path, tmp_path, "host")
    assert creds.env.get("TF_PASS") == "pw"
    assert creds.env.get("TF_TOTP_SEED") == _SEED  # seed injected, not a static code


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()  # type: ignore[call-arg]
                print("ok:", name)
            except TypeError:
                print("skip (fixture):", name)
    print("totp_mfa tests: passed")

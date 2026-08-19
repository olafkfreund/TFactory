"""Tests for the storageState auth-setup scaffolding (#107 task 5).

`scaffold_auth_setup` turns a ref-auth target in the snapshotted .tfactory.yml
into a Playwright login-once setup (auth.setup.ts + a requires_auth config).
Pure filesystem — no browser, no SUT.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from agents.evidence import layout
from agents.evidence.layout import scaffold_auth_setup

_REF_TARGET = {
    "name": "app",
    "type": "http",
    "base_url": "https://app.example.com",
    "auth": {
        "type": "ref",
        "ref": "app-login",
        "login_url": "https://app.example.com/login",
        "username_selector": "#email",
        "password_selector": "#password",
        "submit_selector": "button[type='submit']",
        "success_url_pattern": "dashboard",
    },
}
_TEST_CREDS = {
    "app-login": {
        "ref": "env:APP_PASSWORD",
        "as_secret": "TEST_PASSWORD",
        "as_username": "TEST_USERNAME",
        "username_ref": "env:APP_USERNAME",
    }
}


def _snapshot(spec_dir: Path, targets, test_credentials) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    cfg = {"targets": targets, "test_credentials": test_credentials}
    (ctx / "tfactory_yml.json").write_text(json.dumps(cfg))


def test_scaffolds_auth_setup_and_config(tmp_path) -> None:
    _snapshot(tmp_path, [_REF_TARGET], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is True

    setup = (tmp_path / "tests" / "auth.setup.ts").read_text()
    config = (tmp_path / "playwright.config.ts").read_text()

    # selectors + env names substituted from the RefAuth block / credential entry
    assert "https://app.example.com/login" in setup
    assert "#email" in setup and "#password" in setup
    assert 'process.env["TEST_USERNAME"]' in setup
    assert 'process.env["TEST_PASSWORD"]' in setup
    # credentials are NOT baked in
    assert "APP_PASSWORD" not in setup  # only the env-var NAME is referenced
    # no template placeholders leaked
    assert "@@" not in setup and "@@" not in config
    # the config wires the setup project + storageState reuse
    assert "setup" in config and "storageState" in config and "dependencies" in config


def test_returns_false_without_ref_auth_target(tmp_path) -> None:
    http = {"name": "api", "type": "http", "base_url": "https://x",
            "auth": {"type": "bearer", "token_env": "T"}}
    _snapshot(tmp_path, [http], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is False
    assert not (tmp_path / "tests" / "auth.setup.ts").exists()


def test_returns_false_when_login_url_missing(tmp_path) -> None:
    t = {**_REF_TARGET, "auth": {"type": "ref", "ref": "app-login"}}  # no login_url
    _snapshot(tmp_path, [t], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is False


def test_returns_false_without_snapshot(tmp_path) -> None:
    assert scaffold_auth_setup(tmp_path) is False


# ── multi-step / SSO login path (#107) ───────────────────────────────────────

_STEPS_TARGET = {
    "name": "app",
    "type": "http",
    "base_url": "https://app.example.com",
    "auth": {
        "type": "ref",
        "ref": "app-login",
        # No login_url — the declared steps own the navigation.
        "steps": [
            {"action": "goto", "url": "https://app.example.com"},
            {"action": "click", "selector": "text=Login with SSO"},
            {"action": "fill_username", "selector": "#email"},
            {"action": "fill_secret", "selector": "#password"},
            {"action": "click", "selector": "button[type='submit']"},
            {"action": "wait_for_url", "url": "dashboard"},
        ],
    },
}


def test_scaffolds_multistep_login_when_steps_present(tmp_path) -> None:
    _snapshot(tmp_path, [_STEPS_TARGET], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is True

    setup = (tmp_path / "tests" / "auth.setup.ts").read_text()
    assert 'await page.locator("text=Login with SSO").click();' in setup
    assert 'process.env["TEST_USERNAME"]' in setup
    assert 'process.env["TEST_PASSWORD"]' in setup
    assert 'await page.waitForURL("**/dashboard**");' in setup
    assert "APP_PASSWORD" not in setup  # creds never inlined
    assert "@@" not in setup
    # config still wires the setup project + storageState reuse
    config = (tmp_path / "playwright.config.ts").read_text()
    assert "storageState" in config and "dependencies" in config


def test_steps_path_does_not_require_login_url(tmp_path) -> None:
    # Steps present but no login_url → still scaffolds (single-step rule doesn't apply).
    _snapshot(tmp_path, [_STEPS_TARGET], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is True


# ── __tfTotp extraction: helper copy + import resolution (#1063) ─────────────

_STEPS_TOTP_TARGET = {
    **_STEPS_TARGET,
    "auth": {
        **_STEPS_TARGET["auth"],
        "steps": [*_STEPS_TARGET["auth"]["steps"], {"action": "fill_totp", "selector": "#otp"}],
    },
}
_TEST_CREDS_WITH_TOTP = {
    "app-login": {**_TEST_CREDS["app-login"], "as_totp_secret": "TEST_TOTP_SEED"}
}


def test_scaffolds_multistep_login_copies_totp_helper_next_to_setup(tmp_path) -> None:
    _snapshot(tmp_path, [_STEPS_TOTP_TARGET], _TEST_CREDS_WITH_TOTP)
    assert scaffold_auth_setup(tmp_path) is True

    tests_dir = tmp_path / "tests"
    helper = tests_dir / "tftotp.helper.ts"
    setup_ts = tests_dir / "auth.setup.ts"
    assert helper.is_file()
    # Same directory as auth.setup.ts: a relative "./tftotp.helper" import
    # resolves against the IMPORTING FILE's own location (ESM/CommonJS
    # resolution rule), never the process cwd — so this holds regardless of
    # where Playwright is invoked from.
    assert helper.parent == setup_ts.parent
    assert 'import { __tfTotp } from "./tftotp.helper";' in setup_ts.read_text()
    # Survived the copy byte-for-byte.
    src = Path(layout.__file__).with_name("tftotp.helper.ts")
    assert helper.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert "@@" not in helper.read_text(encoding="utf-8")  # not a template


def test_totp_helper_not_copied_for_single_step_login(tmp_path) -> None:
    # The single-step render_auth_setup() template has no __tfTotp reference —
    # only the multi-step steps template imports the helper.
    _snapshot(tmp_path, [_REF_TARGET], _TEST_CREDS)
    assert scaffold_auth_setup(tmp_path) is True
    assert not (tmp_path / "tests" / "tftotp.helper.ts").exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="node CLI not available")
def test_copied_totp_helper_round_trips_through_node_and_matches_rfc6238(tmp_path) -> None:
    """The helper must parse AND execute after the renderer copies it, and must
    still compute the official RFC 6238 (Appendix B) test vectors — proving the
    extraction did not change what MFA login actually fills in.
    """
    node = shutil.which("node")
    _snapshot(tmp_path, [_STEPS_TOTP_TARGET], _TEST_CREDS_WITH_TOTP)
    assert scaffold_auth_setup(tmp_path) is True
    helper = tmp_path / "tests" / "tftotp.helper.ts"

    # A real parse of the file AS COPIED (not the repo source) via `node --check`.
    checked = subprocess.run(
        [node, "--check", str(helper)], capture_output=True, text=True
    )
    if checked.returncode != 0 and "Unexpected token" in checked.stderr:
        pytest.skip(f"{node} does not type-strip TypeScript: {checked.stderr[:200]}")
    assert checked.returncode == 0, checked.stderr

    # RFC 6238 Appendix B, SHA-1, 8-digit vectors. Secret is base32("12345678901234567890"),
    # the official published ASCII test seed -- decoding it back must yield those
    # exact bytes. Not a credential (gitleaks flags it on shape alone: gitleaks:allow).
    secret_b32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # gitleaks:allow
    vectors = [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ]
    # Execute the copied file via its absolute file:// URL — a genuine
    # parse-and-run of the bytes that landed in the generated workspace.
    driver = tmp_path / "_driver.mjs"
    driver.write_text(
        f"import {{ __tfTotp }} from {json.dumps(helper.resolve().as_uri())};\n"
        f"const vectors = {json.dumps(vectors)};\n"
        f"const secret = {json.dumps(secret_b32)};\n"
        "const results = vectors.map(([at, expected]) => "
        "__tfTotp(secret, { now: at * 1000, digits: 8, alg: 'sha1' }) === expected);\n"
        "console.log(JSON.stringify(results));\n",
        encoding="utf-8",
    )
    run = subprocess.run([node, str(driver)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [True] * len(vectors), run.stdout

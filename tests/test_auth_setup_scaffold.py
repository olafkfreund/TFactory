"""Tests for the storageState auth-setup scaffolding (#107 task 5).

`scaffold_auth_setup` turns a ref-auth target in the snapshotted .tfactory.yml
into a Playwright login-once setup (auth.setup.ts + a requires_auth config).
Pure filesystem — no browser, no SUT.
"""

from __future__ import annotations

import json
from pathlib import Path

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

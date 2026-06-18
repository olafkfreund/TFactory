"""Tests for Playwright storageState auth rendering (#107 task 5).

Backend-pure: renders the bundled templates as strings; no Playwright/runner.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.evidence.layout import (
    render_auth_setup,
    render_auth_setup_steps,
    render_playwright_config,
)
from templates_pkg.engine import load_templates_for_framework

# ── render_playwright_config: auth gating ────────────────────────────────────


def test_config_no_auth_has_no_storage_state(tmp_path: Path) -> None:
    rendered = render_playwright_config(tmp_path / "ev", "http://localhost:3000")
    assert "storageState" not in rendered
    assert 'name: "setup"' not in rendered
    assert 'dependencies: ["setup"]' not in rendered
    assert "@@" not in rendered  # no placeholder leakage


def test_config_with_auth_adds_setup_project_and_storage_state(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev", "http://localhost:3000", requires_auth=True
    )
    assert 'storageState: "state.json"' in rendered
    assert 'name: "setup"' in rendered
    assert "auth\\.setup\\.ts" in rendered
    assert 'dependencies: ["setup"]' in rendered
    assert "@@" not in rendered


def test_config_storage_state_only_on_chromium_not_global_or_setup(tmp_path: Path) -> None:
    """Regression: storageState must live ONLY on the chromium project's `use`.

    If it sits in the global `use`, the `setup` project inherits it and dies trying
    to READ state.json before the login has written it ("Error reading storage
    state ... ENOENT") — the requires_auth flow could never run. It also must be on
    chromium so the saved session is actually loaded. Proven by a live MFA run.
    """
    rendered = render_playwright_config(
        tmp_path / "ev", "http://localhost:3000", requires_auth=True
    )
    # On the chromium project's use line (right after the devices spread).
    assert 'devices["Desktop Chrome"], storageState: "state.json"' in rendered
    # The setup project carries no storageState (it creates it).
    setup_line = next(ln for ln in rendered.splitlines() if 'name: "setup"' in ln)
    assert "storageState" not in setup_line
    # Exactly one storageState in the whole config (not duplicated into global use).
    assert rendered.count("storageState") == 1


def test_config_custom_storage_state_path(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev",
        "http://localhost",
        requires_auth=True,
        storage_state_path="auth/admin.json",
    )
    assert 'storageState: "auth/admin.json"' in rendered


# ── visual-regression: snapshotPathTemplate → the baseline store (#109) ───────


def test_config_without_visual_target_has_no_snapshot_template(tmp_path: Path) -> None:
    rendered = render_playwright_config(tmp_path / "ev", "http://localhost:3000")
    assert "snapshotPathTemplate" not in rendered
    assert "@@" not in rendered  # no placeholder leakage


def test_config_visual_target_points_snapshots_at_the_store(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev", "http://localhost:3000", visual_target="web-app"
    )
    # toHaveScreenshot baselines resolve to the portal-managed store path
    assert (
        'snapshotPathTemplate: "findings/visual_baselines/web-app/{arg}{ext}"'
        in rendered
    )
    assert "@@" not in rendered


def test_config_visual_target_rejects_path_traversal(tmp_path: Path) -> None:
    # a target name that tries to escape the store is rejected (fail-closed)
    import pytest
    from agents.evidence.visual_baseline import VisualBaselineError

    with pytest.raises(VisualBaselineError):
        render_playwright_config(tmp_path / "ev", "http://x", visual_target="../../etc")


# ── render_auth_setup ────────────────────────────────────────────────────────


def test_render_auth_setup_substitutes_all_fields() -> None:
    rendered = render_auth_setup(
        login_url="https://app.example.com/login",
        username_selector="#user",
        password_selector="#pass",
        submit_selector="button[type=submit]",
        success_url_pattern="dashboard",
        username_env="SN_USERNAME",
        secret_env="SN_PASSWORD",
        storage_state_path="state.json",
    )
    assert "https://app.example.com/login" in rendered
    assert "#user" in rendered and "#pass" in rendered
    assert "button[type=submit]" in rendered
    assert "dashboard" in rendered
    # creds read from env vars, never inlined
    assert 'process.env["SN_USERNAME"]' in rendered
    assert 'process.env["SN_PASSWORD"]' in rendered
    assert "storageState({ path: STORAGE_STATE })" in rendered
    assert "@@" not in rendered  # no placeholder leakage


# ── login-flow template now reads injected env vars ──────────────────────────


def test_login_flow_template_reads_injected_env() -> None:
    tmpls = load_templates_for_framework(
        "playwright", include_harvested=False, include_library=False
    )
    login = tmpls["login-flow.spec.ts.tmpl"]
    # requires_auth flips on — the Executor injects creds for this template
    assert login.metadata.requires_auth is True
    # vars match placeholders and include the env-name vars
    body_ph = set(re.findall(r"\$\{(\w+)}", login.body))
    declared = set(login.metadata.vars)
    assert body_ph == declared
    assert {"username_env", "secret_env"} <= declared
    # no hard-coded credentials remain
    assert "test-password-123" not in login.body
    rendered = login.instantiate(
        target_base_url="http://x",
        test_name="t",
        login_path="/login",
        username_selector="#u",
        password_selector="#p",
        submit_selector="#s",
        success_url_pattern="home",
        username_env="U",
        secret_env="S",
    )
    assert "process.env['U']" in rendered
    assert "process.env['S']" in rendered


# ── render_auth_setup_steps: multi-step / SSO login (#107) ───────────────────


def _sso_steps() -> list[dict]:
    return [
        {"action": "goto", "url": "https://app.example.com"},
        {"action": "click", "selector": "text=Login with SSO"},
        {"action": "fill_username", "selector": "#email"},
        {"action": "click", "selector": "#next"},
        {"action": "fill_secret", "selector": "#password"},
        {"action": "fill", "selector": "#tenant", "value": "acme-corp"},
        {"action": "click", "selector": "button[type=submit]"},
        {"action": "wait_for_url", "url": "dashboard"},
    ]


def test_render_steps_emits_each_action_in_order() -> None:
    out = render_auth_setup_steps(
        steps=_sso_steps(),
        username_env="SN_USERNAME",
        secret_env="SN_PASSWORD",
        storage_state_path="state.json",
    )
    assert 'await page.goto("https://app.example.com");' in out
    assert 'await page.locator("text=Login with SSO").click();' in out
    assert 'await page.locator("#email").fill(process.env["SN_USERNAME"] ?? "");' in out
    assert (
        'await page.locator("#password").fill(process.env["SN_PASSWORD"] ?? "");' in out
    )
    assert 'await page.locator("#tenant").fill("acme-corp");' in out  # non-secret literal
    assert 'await page.waitForURL("**/dashboard**");' in out
    # storageState save + no placeholder leakage
    assert "storageState({ path: STORAGE_STATE })" in out
    assert "@@" not in out
    # ordering: goto precedes the SSO click precedes the username fill
    assert out.index("page.goto") < out.index("Login with SSO") < out.index("SN_USERNAME")


def test_render_steps_never_inlines_credentials() -> None:
    out = render_auth_setup_steps(
        steps=_sso_steps(),
        username_env="SN_USERNAME",
        secret_env="SN_PASSWORD",
    )
    # only env-var NAMES appear; no literal credential values
    assert "process.env" in out
    assert "password123" not in out and "hunter2" not in out


def test_render_steps_skips_incomplete_steps() -> None:
    out = render_auth_setup_steps(
        steps=[
            {"action": "click"},  # missing selector → skipped
            {"action": "goto"},  # missing url → skipped
            {"action": "goto", "url": "https://x"},  # valid
        ],
        username_env="U",
        secret_env="S",
    )
    assert 'await page.goto("https://x");' in out
    # the two incomplete steps (click w/o selector, goto w/o url) rendered nothing
    assert "await page.locator" not in out
    assert out.count("await page.goto") == 1


def test_render_steps_escapes_quotes() -> None:
    out = render_auth_setup_steps(
        steps=[{"action": "click", "selector": 'a[title="Go"]'}],
        username_env="U",
        secret_env="S",
    )
    assert r'a[title=\"Go\"]' in out  # double-quotes escaped for the TS literal

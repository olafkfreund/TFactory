"""Keycloak login with TOTP MFA, driven through a real browser (Playwright).

The portals federate to the Keycloak ``factory`` realm. A first hit (or the
portal's "Sign in" button) redirects to the Keycloak login form; after
username+password an OTP form appears. We mint the 6-digit TOTP from the test
user's enrolled secret (``TEST_TOTP_SECRET``) with ``pyotp`` -- so the MFA step
is fully automated, never faked (the real second factor is computed).
"""

from __future__ import annotations

import logging

import pyotp
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from . import config

log = logging.getLogger("keycloak")


def _fill_first(page: Page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.fill(value)
                return True
        except PWTimeout:
            continue
    return False


def _click_first(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.click()
                return True
        except PWTimeout:
            continue
    return False


def on_keycloak(page: Page) -> bool:
    return (
        "/realms/" in page.url
        or "keycloak" in page.url
        or page.locator("#kc-form-login").count() > 0
    )


_USER_SEL = "#username, input[name='username'], input[autocomplete='username']"
_PASS_SEL = "#password, input[name='password'], input[type='password']"
_OTP_SEL = (
    "#otp, input[name='otp'], input[name='totp'], input[autocomplete='one-time-code']"
)
_SUBMIT_SEL = ["#kc-login", "button[type='submit']", "input[type='submit']"]
_ERR_SEL = "#input-error, .kc-feedback-text, .alert-error, .pf-c-alert__title"


def _wait_for(page: Page, selector: str, timeout: int = 12000) -> bool:
    try:
        page.wait_for_selector(selector, timeout=timeout, state="visible")
        return True
    except PWTimeout:
        return False


def ensure_logged_in(page: Page, portal: config.Portal, auth: config.Auth) -> dict:
    """Drive the portal -> "Sign in with SSO" -> Keycloak user/pass -> TOTP MFA.

    Uses explicit waits (the SSO redirect to Keycloak takes a few seconds), so
    interaction never races the redirect. Returns a dict for the report: whether
    MFA was presented and whether login succeeded.
    """
    info: dict = {"mfa_presented": False, "logged_in": False, "notes": []}

    # The portal's own /login page has a "Sign in with SSO" button that starts
    # the Keycloak redirect. Click it (unless we somehow already landed on KC).
    if not on_keycloak(page) and not page.locator(_USER_SEL).count():
        _click_first(
            page,
            [
                "text=Sign in with SSO",
                "button:has-text('SSO')",
                "text=Sign in",
                "text=Log in",
                "text=Login",
                "a:has-text('Sign in')",
            ],
        )
        _wait_for(page, _USER_SEL, timeout=15000)  # wait for the Keycloak form

    if not page.locator(_USER_SEL).count():
        if not on_keycloak(page):
            info["notes"].append(
                "no Keycloak form — session may already be authenticated"
            )
            info["logged_in"] = True
        else:
            info["notes"].append("on Keycloak but no username field found")
        return info

    if not auth.username or not auth.password:
        info["notes"].append("TEST_USER/TEST_PASSWORD not set — cannot complete login")
        return info

    page.locator(_USER_SEL).first.fill(auth.username)
    page.locator(_PASS_SEL).first.fill(auth.password)
    _click_first(page, _SUBMIT_SEL)

    # Wait for either the OTP form, a login error, or navigation off Keycloak.
    page.wait_for_timeout(1500)
    _wait_for(page, f"{_OTP_SEL}, {_ERR_SEL}", timeout=10000)

    if page.locator(_OTP_SEL).count():
        info["mfa_presented"] = True
        if not auth.totp_secret:
            info["notes"].append("OTP required but TEST_TOTP_SECRET not set")
            return info
        totp = pyotp.TOTP(auth.totp_secret)
        # The OTP is time-boxed (30s). A code minted just before a window
        # boundary can be rejected by the time it's submitted, and clock skew
        # between the Job pod and Keycloak compounds it. Retry with a freshly
        # minted code (waiting a beat to land in a clean window) so a transient
        # "Invalid authenticator code" doesn't fail the whole run.
        for attempt in range(3):
            field = page.locator(_OTP_SEL).first
            if not field.count():
                break  # OTP accepted — moved off the form
            field.fill("")
            field.fill(totp.now())
            _click_first(page, _SUBMIT_SEL)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PWTimeout:
                page.wait_for_timeout(3000)
            if not page.locator(_OTP_SEL).count():
                break  # left the OTP form → success
            if attempt < 2:
                info["notes"].append(f"OTP retry {attempt + 1} (code rejected)")
                page.wait_for_timeout(2000)  # let the time window roll over

    info["logged_in"] = not on_keycloak(page) and not page.locator(_USER_SEL).count()
    if not info["logged_in"]:
        err = page.locator(_ERR_SEL).first
        if err.count():
            info["notes"].append(f"login error: {err.inner_text()[:120]}")
    return info

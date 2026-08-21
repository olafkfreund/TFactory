"""The OAuth result page must ADDRESS its postMessage, not broadcast it (#1053).

``_oauth_result_html`` renders the page the OAuth popup finishes on. It posts the
outcome -- which carries the connected email address and the provider -- back to
``window.opener``. With a ``'*'`` targetOrigin the browser delivers that payload
to whatever is loaded in the opener at that moment, and the popup's lifetime
spans a full third-party OAuth round trip, so "whatever is loaded" is not
something this page gets to assume.

These tests pin the outbound half: the half that stops the address leaving at
all.

The origin is taken from ``CORS_ORIGINS``, never from a request header -- a
targetOrigin an attacker can set is ``'*'`` with extra steps.

Same defect and same shape as AIFactory#1285 and PFactory#541; this is the
third fork of the file. Mutation check: restore ``}}, '*');`` as the
postMessage target and ``test_the_wildcard_target_origin_is_gone`` goes red.
"""

from __future__ import annotations

import pytest

# get_settings comes from its own module, not through ``email``: it is only
# re-exported there, and mypy --strict (no_implicit_reexport) rejects reaching
# it via the importing module. Same singleton either way, so the patch below
# still reaches the object the route reads.
from server.config import get_settings
from server.routes import email


def _post(origin: str) -> str:
    """The exact postMessage call this repo emits for ``origin``.

    Built with the module's own ``_js_string`` rather than hard-coding a
    quote style: the sibling forks escape with different quoting, and an
    assertion that hard-codes one silently tests nothing in the other.
    """
    return f"window.opener.postMessage(payload, {email._js_string(origin)});"


def _wildcard_target() -> str:
    """The wildcard target as this repo would render it, if it ever did."""
    return f", {email._js_string('*')})"


PORTAL = "https://portal.example.com"


def _render(monkeypatch: pytest.MonkeyPatch, origins: list[str]) -> str:
    """Render the result page with ``origins`` configured, returning its HTML."""
    settings = get_settings()
    monkeypatch.setattr(settings, "CORS_ORIGINS", origins)
    response = email._oauth_result_html(
        success=True,
        message="Connected",
        email="alice@example.com",
        provider="outlook",
    )
    return bytes(response.body).decode()


def test_the_configured_origin_is_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _render(monkeypatch, [PORTAL])
    assert _post(PORTAL) in html


def test_the_wildcard_target_origin_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug itself. ``'*'`` must not appear as a postMessage target."""
    html = _render(monkeypatch, [PORTAL])
    assert _wildcard_target() not in html
    # Both quote styles, so this stays a real check if the escaper changes.
    assert ", '*')" not in html
    assert ', "*")' not in html


def test_every_configured_origin_is_addressed_individually(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One targeted post per configured origin, rather than one guess.

    A deployment may legitimately serve the portal from more than one origin,
    and postMessage takes exactly one target. The browser delivers only the
    call whose origin matches the opener, so the rest are silent no-ops -- which
    is why the loop is correct where picking the first entry would be a guess.
    """
    second = "https://portal.internal.example"
    html = _render(monkeypatch, [PORTAL, second])
    assert _post(PORTAL) in html
    assert _post(second) in html


def test_a_wildcard_in_the_configured_origins_is_dropped_not_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``APP_CORS_ORIGINS='*'`` must not become a wildcard targetOrigin.

    The CORS layer tolerates a wildcard by dropping credentials; that tolerance
    must not leak into postMessage, where the wildcard IS the vulnerability.
    """
    html = _render(monkeypatch, ["*", PORTAL])
    assert _wildcard_target() not in html
    assert _post(PORTAL) in html


def test_no_configured_origin_means_no_post_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: render the outcome, post nothing. Never fall back to '*'."""
    html = _render(monkeypatch, [])
    assert "postMessage" not in html
    assert "Connected" in html


def test_the_address_is_not_in_a_page_that_posts_nowhere_useful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The payload still carries the address, so the target is what protects it.

    Stated as a test so nobody 'fixes' #1053 by deleting the email field and
    calls the leak closed: the field is what the settings page needs, and the
    targetOrigin is what keeps it addressed.
    """
    html = _render(monkeypatch, [PORTAL])
    assert "alice@example.com" in html
    assert _post(PORTAL) in html

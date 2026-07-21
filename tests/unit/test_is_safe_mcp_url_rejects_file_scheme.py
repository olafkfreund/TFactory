"""AC#6: Tests cover ``file://`` -- a non-http scheme must be refused.

``_is_safe_mcp_url`` is the SSRF guard for request-supplied MCP server URLs.
Two distinct refusal shapes are specified and are asserted here:

* a *syntactically valid but non-http* URL such as ``file:///etc/passwd``
  raises ``HTTPException`` with status 400 (an actively hostile input -- it
  would otherwise let the health check read off the local filesystem);
* an *empty or whitespace-only* URL returns ``False`` -- there is nothing to
  refuse, so the guard reports "not safe" without raising.

Crucially, neither path may reach DNS: scheme validation happens before
``socket.getaddrinfo`` is called. Every test installs a resolver stub that
fails loudly if it is invoked, so a future refactor that resolves first and
validates the scheme later fails this file instead of silently regressing.

Target: apps/web-server/server/routes/git.py::_is_safe_mcp_url

The module is imported as a module (``from server.routes import git``) rather
than importing the private helper by name, so the pre-flight import check
resolves against a public attribute path.
"""

import pytest
from fastapi import HTTPException

from server.routes import git


NON_HTTP_URLS = [
    "file:///etc/passwd",
    "file://localhost/etc/shadow",
    "ftp://example.com/payload",
    "gopher://127.0.0.1:11211/_stats",
    "//example.com/no-scheme-at-all",
]

NON_HTTP_IDS = [
    "file-etc-passwd",
    "file-with-authority",
    "ftp",
    "gopher",
    "scheme-relative",
]

BLANK_URLS = ["", "   ", "\t", "\n", " \t\n "]

BLANK_IDS = ["empty", "spaces", "tab", "newline", "mixed-whitespace"]


@pytest.fixture(autouse=True)
def resolver_must_not_be_called(monkeypatch):
    """Make any DNS resolution attempt an explicit failure.

    Patched at the import site of the module under test (``git.socket``), so
    only this module's resolution is affected. Autouse: no test in this file
    supplies a URL that is allowed to reach the resolver.
    """

    def fail_on_resolve(host, port, *args, **kwargs):
        raise AssertionError(
            f"_is_safe_mcp_url resolved {host!r} before validating the URL scheme"
        )

    monkeypatch.setattr(git.socket, "getaddrinfo", fail_on_resolve)


@pytest.mark.parametrize("url", NON_HTTP_URLS, ids=NON_HTTP_IDS)
def test_is_safe_mcp_url_non_http_scheme_raises_http_400(url):
    """A non-http(s) URL is actively refused with a 400, never accepted."""
    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url(url)

    assert exc_info.value.status_code == 400


def test_is_safe_mcp_url_file_scheme_detail_reports_invalid_url():
    """The refusal is attributed to the URL itself, not to a resolution failure."""
    with pytest.raises(HTTPException) as exc_info:
        git._is_safe_mcp_url("file:///etc/passwd")

    assert "invalid" in str(exc_info.value.detail).lower()


def test_is_safe_mcp_url_file_scheme_never_returns_a_value():
    """Fail-closed proof: ``file://`` cannot fall through to a truthy verdict.

    A guard that merely logged the bad scheme and returned would let the
    caller proceed; only raising stops the health check.
    """
    with pytest.raises(HTTPException):
        result = git._is_safe_mcp_url("file:///etc/passwd")
        pytest.fail(f"expected HTTPException, got return value {result!r}")


@pytest.mark.parametrize("url", BLANK_URLS, ids=BLANK_IDS)
def test_is_safe_mcp_url_blank_url_returns_false(url):
    """An empty or whitespace-only URL is reported unsafe by return, not by raise."""
    assert git._is_safe_mcp_url(url) is False


@pytest.mark.parametrize("url", BLANK_URLS, ids=BLANK_IDS)
def test_is_safe_mcp_url_blank_url_does_not_raise(url):
    """The blank-URL path is distinct from the 400 path -- no exception escapes."""
    try:
        git._is_safe_mcp_url(url)
    except HTTPException as exc:  # pragma: no cover - only on regression
        pytest.fail(f"blank URL {url!r} raised HTTPException({exc.status_code})")


def test_is_safe_mcp_url_none_url_returns_false():
    """A missing URL (``None``) is short-circuited as unsafe, not dereferenced."""
    assert git._is_safe_mcp_url(None) is False

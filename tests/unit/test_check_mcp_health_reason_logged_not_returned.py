"""AC#5: the refusal reason is *logged*, not *returned*.

``check_mcp_health`` catches the ``HTTPException`` raised by the SSRF guard and
answers with an opaque ``status: "unknown"`` envelope. That split has two halves
and both are load-bearing:

* **Logged** — an operator debugging "why is my MCP server showing unknown?" has
  to be able to find the reason server-side. If the ``except`` block silently
  swallowed the exception, the refusal would be invisible and indistinguishable
  from a genuinely unreachable server.
* **Not returned** — the reason is an SSRF oracle. A caller who can tell
  "unresolvable host" from "disallowed address" from "invalid scheme" can map
  internal network reachability one probe at a time. The refused URL itself is
  equally sensitive: echoing it back confirms what was probed.

The sibling ``test_check_mcp_health_unknown_shape.py`` pins the response *shape*.
This file pins the *logging* half plus the non-leak boundary: the detail must
land in the module logger's records, and must appear nowhere in the payload.

Target: apps/web-server/server/routes/git.py::check_mcp_health

The module is imported as a module (``from server.routes import git``) — matching
the sibling test files in this spec — so ``monkeypatch.setattr`` lands on the same
attribute the endpoint body resolves at call time.
"""

import asyncio
import json
import logging

import pytest
from fastapi import HTTPException

from server.routes import git


# Every distinct way ``_is_safe_mcp_url`` can refuse. All must be logged, and
# none may reach the client.
REFUSAL_DETAILS = [
    "Invalid MCP server URL",
    "Disallowed MCP server URL",
    "Could not resolve MCP server hostname",
    "Error resolving MCP server hostname",
    "Invalid resolved IP address",
    "Disallowed MCP server address",
]

REFUSAL_IDS = [
    "invalid-url",
    "blocklisted-host",
    "unresolvable-host",
    "resolver-error",
    "invalid-resolved-ip",
    "disallowed-address",
]

EXPECTED_MESSAGE = "Cannot check server"

SENSITIVE_URL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


def _server(url=SENSITIVE_URL, server_id="srv-1"):
    """Build an ``http``-type ``McpServerConfig`` — the only branch AC#5 covers.

    A ``command``-type server never reaches the guard at all, so the type must be
    pinned to ``http`` or the test would pass without exercising the path.
    """
    return git.McpServerConfig(
        id=server_id,
        name="metadata-probe",
        type="http",
        url=url,
    )


@pytest.fixture
def refuse_url(monkeypatch):
    """Install a ``_is_safe_mcp_url`` that refuses with a chosen ``detail``.

    Mocking the *dependency* (the guard), not the subject: ``check_mcp_health``
    itself runs for real, which is what the log-vs-return split lives in.
    """

    def _install(detail="Disallowed MCP server address", status_code=400):
        def fake_guard(url):
            raise HTTPException(status_code=status_code, detail=detail)

        monkeypatch.setattr(git, "_is_safe_mcp_url", fake_guard)
        return fake_guard

    return _install


@pytest.fixture
def allow_url(monkeypatch):
    """Install a ``_is_safe_mcp_url`` that accepts, for the contrast test."""

    def fake_guard(url):
        return True

    monkeypatch.setattr(git, "_is_safe_mcp_url", fake_guard)
    return fake_guard


@pytest.fixture
def forbid_network(monkeypatch):
    """Explode if the refusal path ever opens a socket.

    A regression that dropped the early ``return`` would fall through to
    ``urlopen`` and actually fetch the metadata address — the precise bug the
    SSRF guard exists to prevent.
    """
    import urllib.request

    def exploded(*args, **kwargs):  # pragma: no cover - asserted via failure
        raise AssertionError("a refused URL must never be fetched")

    monkeypatch.setattr(urllib.request, "urlopen", exploded)


@pytest.fixture
def capture_git_logs(caplog):
    """Capture WARNING+ records from the module under test's own logger.

    Scoped to ``git.__name__`` so an unrelated library logging the same string
    cannot make the "it was logged" assertions pass by accident.
    """
    caplog.set_level(logging.WARNING, logger=git.__name__)
    return caplog


@pytest.mark.parametrize("detail", REFUSAL_DETAILS, ids=REFUSAL_IDS)
def test_check_mcp_health_logs_the_refusal_reason(
    refuse_url, forbid_network, capture_git_logs, detail
):
    """Half one: every refusal reason reaches the server-side log.

    Without this, an operator sees only a grey "unknown" chip with no way to
    learn whether the URL was blocklisted, unresolvable, or simply malformed.
    """
    refuse_url(detail=detail)

    asyncio.run(git.check_mcp_health(_server()))

    logged = "\n".join(r.getMessage() for r in capture_git_logs.records)
    assert detail in logged


@pytest.mark.parametrize("detail", REFUSAL_DETAILS, ids=REFUSAL_IDS)
def test_check_mcp_health_does_not_return_the_refusal_reason(
    refuse_url, forbid_network, detail
):
    """Half two: the same reason appears nowhere in the response payload.

    Serialising the whole envelope catches a leak buried in any nested field,
    not just the ``message`` a targeted assertion would inspect.
    """
    refuse_url(detail=detail)

    result = asyncio.run(git.check_mcp_health(_server()))

    assert detail not in json.dumps(result)
    assert result["data"]["message"] == EXPECTED_MESSAGE


def test_check_mcp_health_logs_the_refusal_at_warning_level_or_higher(
    refuse_url, forbid_network, capture_git_logs
):
    """A refused SSRF probe is an operational signal, not DEBUG noise.

    Logged below WARNING it would be filtered out by default production config,
    making the "it is logged" half of AC#5 true only on paper.
    """
    refuse_url(detail="Disallowed MCP server address")

    asyncio.run(git.check_mcp_health(_server()))

    assert [r for r in capture_git_logs.records if r.levelno >= logging.WARNING]


def test_check_mcp_health_does_not_return_the_refused_url(refuse_url, forbid_network):
    """Boundary: the probed URL is itself sensitive and must not be echoed.

    Reflecting ``http://169.254.169.254/...`` back would confirm to the caller
    exactly which internal target was attempted.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server(url=SENSITIVE_URL)))

    assert SENSITIVE_URL not in json.dumps(result)


def test_check_mcp_health_logs_nothing_when_the_url_is_allowed(
    allow_url, monkeypatch, capture_git_logs
):
    """Contrast: the warning is caused by the refusal, not emitted on every call.

    Without this, a module that warned unconditionally would satisfy the
    "is logged" test while carrying no information at all.
    """
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: object())

    result = asyncio.run(git.check_mcp_health(_server(url="http://10.0.0.5:8080/")))

    assert result["data"]["status"] == "healthy"
    assert [r for r in capture_git_logs.records if "SSRF" in r.getMessage()] == []

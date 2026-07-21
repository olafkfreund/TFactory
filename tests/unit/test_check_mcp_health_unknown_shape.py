"""AC#5: ``check_mcp_health`` keeps its existing ``status: "unknown"`` response
shape when ``_is_safe_mcp_url`` refuses the URL.

The SSRF guard was tightened so refused URLs never reach the network. The risk
that change introduces is at the *boundary*: if the refusal escaped as an
``HTTPException`` (a 400), or as ``success: False``, or with the guard's own
``detail`` string echoed back, the portal's MCP settings panel would either show
an error toast where it used to show a grey "unknown" chip, or would leak *why*
the URL was refused — which is itself an SSRF probing oracle (an attacker learns
whether 169.254.169.254 resolved, was reserved, or was unresolvable).

This file pins all three halves of that claim:

* the refusal is swallowed — the coroutine returns rather than raising,
* the returned envelope is exactly ``{"success": True, "data": {"serverId",
  "status": "unknown", "message": "Cannot check server"}}``, and
* the guard's ``detail`` never appears anywhere in the returned payload.

Target: apps/web-server/server/routes/git.py::check_mcp_health

The module is imported as a module (``from server.routes import git``) rather
than importing the endpoint by name, matching the sibling test files in this
spec so ``monkeypatch.setattr`` lands on the same attribute the endpoint body
looks up at call time.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException

from server.routes import git


# The guard's failure modes, as raised by ``_is_safe_mcp_url``. Every one of
# these must collapse to the SAME opaque "unknown" envelope — a client that
# could tell them apart would have an SSRF oracle.
REFUSAL_DETAILS = [
    "Invalid MCP server URL",
    "Disallowed MCP server URL",
    "Could not resolve MCP server hostname",
    "MCP server URL resolves to a disallowed address",
]

EXPECTED_MESSAGE = "Cannot check server"


def _server(url="http://169.254.169.254/latest/meta-data", server_id="srv-1"):
    """Build an http-type ``McpServerConfig`` — the only branch AC#5 covers.

    A ``command``-type server never reaches the guard at all, so the type must
    be pinned to ``http`` for this test to exercise the intended path.
    """
    return git.McpServerConfig(
        id=server_id,
        name="metadata-probe",
        type="http",
        url=url,
    )


@pytest.fixture
def refuse_url(monkeypatch):
    """Make ``_is_safe_mcp_url`` refuse, and record that it was consulted.

    Returns an installer taking the ``detail`` the guard raises with. The
    installed fake counts its calls so a test can prove the endpoint actually
    routed through the guard instead of short-circuiting on the URL string.
    """

    def _install(detail="Disallowed MCP server URL", status_code=400):
        def fake_guard(url):
            fake_guard.calls += 1
            fake_guard.urls.append(url)
            raise HTTPException(status_code=status_code, detail=detail)

        fake_guard.calls = 0
        fake_guard.urls = []
        monkeypatch.setattr(git, "_is_safe_mcp_url", fake_guard)
        return fake_guard

    return _install


@pytest.fixture
def forbid_network(monkeypatch):
    """Fail loudly if the endpoint ever opens a socket on the refusal path.

    Without this, a regression that dropped the ``return`` after the ``except``
    block would fall through to ``urlopen`` and quietly make a real request to
    the metadata address — the exact bug AC#5 exists to prevent.
    """
    import urllib.request

    def exploded(*args, **kwargs):  # pragma: no cover - asserted via failure
        raise AssertionError("refused URL must never be fetched")

    monkeypatch.setattr(urllib.request, "urlopen", exploded)


def test_check_mcp_health_refused_url_returns_unknown_envelope(
    refuse_url, forbid_network
):
    """AC#5 core: the whole returned dict matches the pre-change shape exactly.

    Asserted as one equality rather than key-by-key so an *added* field (say a
    ``reason``) fails here too — extra keys are as much a contract break as
    missing ones when the frontend renders on shape.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server(server_id="srv-42")))

    assert result == {
        "success": True,
        "data": {
            "serverId": "srv-42",
            "status": "unknown",
            "message": EXPECTED_MESSAGE,
        },
    }


def test_check_mcp_health_refused_url_does_not_raise(refuse_url, forbid_network):
    """The ``HTTPException`` is swallowed, not propagated to FastAPI.

    If it escaped, the route would answer 400 instead of 200 and the settings
    panel would surface an error banner where it used to show "unknown".
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["success"] is True


def test_check_mcp_health_consults_the_guard_with_the_supplied_url(
    refuse_url, forbid_network
):
    """The "unknown" answer is earned by calling the guard, not hardcoded.

    An endpoint that string-matched suspicious URLs itself would return the
    right shape here while leaving the real guard unwired.
    """
    guard = refuse_url()
    url = "http://metadata.google.internal/computeMetadata/v1/"

    asyncio.run(git.check_mcp_health(_server(url=url)))

    assert guard.calls == 1
    assert guard.urls == [url]


@pytest.mark.parametrize(
    "detail",
    REFUSAL_DETAILS,
    ids=[
        "invalid-scheme",
        "blocklisted-host",
        "unresolvable-host",
        "disallowed-address",
    ],
)
def test_check_mcp_health_refusal_reason_is_not_leaked_to_the_client(
    refuse_url, forbid_network, detail
):
    """Boundary: no refusal ``detail`` reaches the client, for any failure mode.

    Serialising the payload catches a leak hidden anywhere in the structure,
    not just in the ``message`` field a targeted assertion would check.
    """
    refuse_url(detail=detail)

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["data"]["message"] == EXPECTED_MESSAGE
    assert detail not in json.dumps(result)


@pytest.mark.parametrize(
    "detail",
    REFUSAL_DETAILS,
    ids=[
        "invalid-scheme",
        "blocklisted-host",
        "unresolvable-host",
        "disallowed-address",
    ],
)
def test_check_mcp_health_every_refusal_reason_yields_the_same_status(
    refuse_url, forbid_network, detail
):
    """All refusal reasons collapse to one indistinguishable ``unknown``.

    Distinguishable responses would let a caller enumerate internal network
    reachability one URL at a time.
    """
    refuse_url(detail=detail)

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["data"]["status"] == "unknown"


def test_check_mcp_health_refused_url_echoes_the_requested_server_id(
    refuse_url, forbid_network
):
    """``serverId`` is the caller's id, so the UI can key the row it updates.

    A constant or missing id would leave the panel unable to attribute the
    result to the server the user asked about.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server(server_id="mcp-github-9")))

    assert result["data"]["serverId"] == "mcp-github-9"


def test_check_mcp_health_unknown_status_differs_from_the_unhealthy_status(
    refuse_url, forbid_network
):
    """Contrast: a refusal is "unknown", which is NOT the failure status.

    "unhealthy" means "we asked and it did not answer"; "unknown" means "we
    refused to ask". Collapsing them would misreport a blocked URL as a down
    server and send an operator chasing a phantom outage.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["data"]["status"] != "unhealthy"

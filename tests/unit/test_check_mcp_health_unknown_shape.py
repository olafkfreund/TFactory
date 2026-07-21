"""AC#5: ``check_mcp_health`` keeps its existing ``status: "unknown"`` response
shape when ``_is_safe_mcp_url`` refuses the URL.

The SSRF guard added in this change sits *in front of* the network call, so the
regression risk is entirely at the response boundary. Three ways it could break:

* the ``HTTPException`` escapes and FastAPI answers **400** where the portal's
  MCP settings panel used to receive a 200 with a grey "unknown" chip,
* the envelope changes shape (``success: False``, a renamed key, an *added*
  ``reason`` field) and the frontend — which renders on shape — breaks, or
* execution falls through the ``except`` block to ``urlopen`` and the refused
  URL gets fetched anyway, defeating the guard entirely.

This file pins the happy-path shape plus those boundaries.

Target: apps/web-server/server/routes/git.py::check_mcp_health

The module is imported as a module (``from server.routes import git``) rather
than importing the endpoint by name, so ``monkeypatch.setattr`` lands on the
same module-global attribute the endpoint body resolves at call time.
"""

import asyncio

import pytest
from fastapi import HTTPException

from server.routes import git


# The exact envelope the endpoint returns on a refusal, per the AC. Kept as
# module constants so a drift shows up as one obvious diff rather than being
# spread across every assertion.
EXPECTED_STATUS = "unknown"
EXPECTED_MESSAGE = "Cannot check server"

# Every distinct ``detail`` ``_is_safe_mcp_url`` raises with. AC#5 requires all
# of them to collapse to the same opaque answer — a client able to tell them
# apart would have an SSRF oracle for internal network reachability.
REFUSAL_DETAILS = [
    "Invalid MCP server URL",
    "Disallowed MCP server URL",
    "Could not resolve MCP server hostname",
    "Error resolving MCP server hostname",
    "Invalid resolved IP address",
    "Disallowed MCP server address",
]


def _server(url="http://169.254.169.254/latest/meta-data", server_id="srv-1"):
    """Build an ``http``-type ``McpServerConfig`` — the only branch AC#5 covers.

    A ``command``-type server returns "unknown" without ever consulting the
    guard, so the type must be pinned to ``http`` for these tests to exercise
    the refusal path rather than the unrelated command-server path.
    """
    return git.McpServerConfig(
        id=server_id,
        name="metadata-probe",
        type="http",
        url=url,
    )


@pytest.fixture
def refuse_url(monkeypatch):
    """Install a ``_is_safe_mcp_url`` that refuses, and record its calls.

    Returns an installer so each test picks the ``detail``/``status_code`` the
    guard raises with. The fake counts calls and captures the URL it was handed,
    letting a test prove the endpoint really routed through the guard instead of
    hardcoding "unknown" or string-matching the URL itself.
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
    """Fail loudly if the refusal path ever opens a socket.

    A regression dropping the ``return`` inside the ``except`` block would fall
    through to ``urllib.request.urlopen`` and issue a real request to the
    metadata address — the precise failure this guard exists to prevent. Without
    this fixture that bug would still produce a plausible-looking response and
    the other assertions would not catch it.
    """
    import urllib.request

    def exploded(*args, **kwargs):  # pragma: no cover - asserted via failure
        raise AssertionError("a refused MCP URL must never be fetched")

    monkeypatch.setattr(urllib.request, "urlopen", exploded)


def test_check_mcp_health_refused_url_returns_unknown_envelope(
    refuse_url, forbid_network
):
    """AC#5 core: the full returned dict matches the pre-change shape exactly.

    Asserted as a single equality rather than key-by-key so an *added* field
    (e.g. a leaked ``reason``) fails here too — for a frontend that renders on
    shape, extra keys are as much a contract break as missing ones.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server(server_id="srv-42")))

    assert result == {
        "success": True,
        "data": {
            "serverId": "srv-42",
            "status": EXPECTED_STATUS,
            "message": EXPECTED_MESSAGE,
        },
    }


def test_check_mcp_health_refused_url_does_not_raise(refuse_url, forbid_network):
    """The guard's ``HTTPException`` is swallowed, not propagated to FastAPI.

    If it escaped, the route would answer 400 instead of 200 and the settings
    panel would surface an error banner where it used to show an "unknown" chip.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["success"] is True


def test_check_mcp_health_refused_url_echoes_the_requested_server_id(
    refuse_url, forbid_network
):
    """``serverId`` is the caller's own id, so the UI can key the row it updates.

    A constant or omitted id would leave the panel unable to attribute the
    result to the server the operator asked about.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server(server_id="mcp-github-9")))

    assert result["data"]["serverId"] == "mcp-github-9"


def test_check_mcp_health_consults_the_guard_with_the_supplied_url(
    refuse_url, forbid_network
):
    """The "unknown" answer is earned by calling the guard, not hardcoded.

    An endpoint that pattern-matched suspicious URLs inline would satisfy the
    shape assertions above while leaving the real guard unwired.
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
        "resolver-error",
        "invalid-resolved-ip",
        "disallowed-address",
    ],
)
def test_check_mcp_health_every_refusal_reason_yields_the_same_envelope(
    refuse_url, forbid_network, detail
):
    """Boundary: every distinct refusal reason collapses to one answer.

    Distinguishable responses would let a caller enumerate internal network
    reachability one probe at a time, which is exactly the oracle AC#5 closes.
    """
    refuse_url(detail=detail)

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["data"]["status"] == EXPECTED_STATUS
    assert result["data"]["message"] == EXPECTED_MESSAGE


def test_check_mcp_health_unknown_status_differs_from_the_unhealthy_status(
    refuse_url, forbid_network
):
    """Contrast: a refusal is "unknown", which is NOT the failure status.

    "unhealthy" means "we asked and it did not answer"; "unknown" means "we
    declined to ask". Collapsing the two would misreport a blocked URL as a
    downed server and send an operator chasing a phantom outage.
    """
    refuse_url()

    result = asyncio.run(git.check_mcp_health(_server()))

    assert result["data"]["status"] != "unhealthy"

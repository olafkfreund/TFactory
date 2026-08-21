"""The outbound-URL guard, in both postures, plus the per-route posture choice.

The point of the strict/permissive split is that the fleet runs a self-hosted
Ollama on a private address. A guard that blocks RFC-1918 outright is not
"more secure" here -- it breaks the product, gets reverted, and leaves nothing.
So the permissive posture keeps only the guards that cost no legitimate use.

The second half of this file is the part that would actually catch a bad
refactor: it pins WHICH posture each route family uses. A guard applied with
the wrong posture passes every test above and still breaks local Ollama.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from factory_common.url_safety import (  # noqa: E402
    _NoRedirect,
    assert_safe_outbound_url,
)
from fastapi import HTTPException  # noqa: E402
from server.routes.git import (  # noqa: E402
    _safe_mcp_url,
    _safe_ollama_base_url,
)
from server.routes.llm_endpoints import _safe_probe_models_url  # noqa: E402
from server.routes.settings_api_profiles import _safe_profile_models_url  # noqa: E402
from server.routes.settings_llm_providers import _safe_local_base_url  # noqa: E402

# 169.254.169.254 is the cloud metadata address. Both postures must refuse it;
# that is the whole reason the permissive posture is not simply "no check".
METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


def test_metadata_is_refused_in_both_postures() -> None:
    # Plain loops rather than @pytest.mark.parametrize: this file is new, so the
    # ratchet holds it to mypy --strict, where an untyped decorator makes the
    # whole test function untyped.
    for allow_private in (False, True):
        with pytest.raises(ValueError, match="link-local/metadata"):
            assert_safe_outbound_url(METADATA, allow_private=allow_private)


def test_non_http_schemes_are_refused_in_both_postures() -> None:
    for url in ("file:///etc/passwd", "gopher://x/", "ftp://h/f"):
        for allow_private in (False, True):
            with pytest.raises(ValueError, match="unsupported URL scheme"):
                assert_safe_outbound_url(url, allow_private=allow_private)


def test_strict_posture_refuses_loopback() -> None:
    with pytest.raises(ValueError, match="non-public"):
        assert_safe_outbound_url("http://127.0.0.1:8080/v1/models")


def test_permissive_posture_allows_the_self_hosted_ollama_case() -> None:
    """The regression this split exists to prevent: a local Ollama must work."""
    assert_safe_outbound_url("http://127.0.0.1:11434/api/tags", allow_private=True)
    assert_safe_outbound_url("http://10.0.0.5:11434/api/tags", allow_private=True)


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(ValueError, match="no host"):
        assert_safe_outbound_url("http:///nohost")


def test_an_unresolvable_host_is_refused_rather_than_attempted() -> None:
    with pytest.raises(ValueError, match="cannot resolve host"):
        assert_safe_outbound_url("http://no-such-host.invalid/x", allow_private=True)


def test_the_no_redirect_handler_refuses_30x() -> None:
    """A public URL that 302s to the metadata service defeats a pre-flight check."""
    req = urllib.request.Request("http://example.invalid/start")
    with pytest.raises(urllib.error.HTTPError, match="Redirect blocked"):
        _NoRedirect().redirect_request(req, None, 302, "Found", {}, METADATA)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Per-route posture. These are the tests that fail if someone "hardens" the
# local-provider routes into the strict posture and breaks self-hosted Ollama,
# or relaxes the cloud-profile routes into the permissive one.
# --------------------------------------------------------------------------


def test_ollama_route_helper_keeps_localhost_working() -> None:
    assert _safe_ollama_base_url("http://localhost:11434") == "http://localhost:11434"
    assert _safe_ollama_base_url(None) == "http://localhost:11434"
    # Path/query/fragment are dropped so the appended /api/... cannot be moved.
    assert _safe_ollama_base_url("http://127.0.0.1:11434/x#") == "http://127.0.0.1:11434"


def test_ollama_route_helper_refuses_metadata_and_bad_schemes() -> None:
    with pytest.raises(HTTPException) as exc:
        _safe_ollama_base_url("http://169.254.169.254/")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _safe_ollama_base_url("file:///etc/passwd")


def test_local_provider_helper_keeps_localhost_working() -> None:
    assert _safe_local_base_url("http://localhost:11434") == "http://localhost:11434"
    assert _safe_local_base_url("http://localhost:8080") == "http://localhost:8080"


def test_local_provider_helper_refuses_metadata_and_bad_schemes() -> None:
    with pytest.raises(HTTPException) as exc:
        _safe_local_base_url("http://169.254.169.254/")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        _safe_local_base_url("gopher://x/")


def test_api_profile_helper_uses_the_strict_posture() -> None:
    """An API profile is a cloud endpoint; it must not reach inside the network."""
    with pytest.raises(ValueError, match="non-public"):
        _safe_profile_models_url("http://127.0.0.1:8080")
    with pytest.raises(ValueError, match="link-local/metadata"):
        _safe_profile_models_url("http://169.254.169.254")


def test_mcp_helper_keeps_the_local_and_lan_case_working() -> None:
    """MCP servers overwhelmingly run on localhost; the permissive posture is
    the whole reason this helper is not simply the strict guard (#1047)."""
    assert _safe_mcp_url("http://localhost:3000/mcp") == "http://localhost:3000/mcp"
    assert _safe_mcp_url("http://127.0.0.1:8931/sse") == "http://127.0.0.1:8931/sse"


def test_mcp_helper_refuses_metadata_and_bad_schemes() -> None:
    with pytest.raises(ValueError, match="link-local/metadata"):
        _safe_mcp_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError, match="Unsupported or invalid"):
        _safe_mcp_url("file:///etc/passwd")


def test_probe_helper_keeps_a_byo_local_endpoint_working() -> None:
    """LM Studio on localhost is the ordinary case for a BYO LLM endpoint."""
    assert (
        _safe_probe_models_url("http://localhost:1234")
        == "http://localhost:1234/v1/models"
    )
    # A path prefix survives -- an OpenAI-compatible gateway legitimately has
    # one, and collapsing to scheme://netloc would break every such endpoint.
    assert (
        _safe_probe_models_url("http://10.0.0.5:8000/openai/")
        == "http://10.0.0.5:8000/openai/v1/models"
    )


def test_probe_helper_refuses_metadata_and_bad_schemes() -> None:
    with pytest.raises(ValueError, match="link-local/metadata"):
        _safe_probe_models_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError, match="Invalid base URL"):
        _safe_probe_models_url("file:///etc/passwd")


def test_probe_helper_drops_a_fragment_that_would_truncate_the_appended_path() -> None:
    """Without this, "http://host#" makes the fetched URL "http://host#/v1/models"
    -- the server sees "/" and the appended path is thrown away client-side."""
    assert (
        _safe_probe_models_url("http://127.0.0.1:1234/base#x")
        == "http://127.0.0.1:1234/base/v1/models"
    )

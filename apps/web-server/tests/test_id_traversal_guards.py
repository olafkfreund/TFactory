"""Request-supplied id guards must reject ``.`` and ``..`` (#866).

Seven routes copy-pasted the same charset guard::

    re.compile(r"^[A-Za-z0-9._-]+$")

and the comment above each one claims it stops a crafted id traversing out of
the workspace root. It does not: ``.`` and ``..`` are both full matches, and
both are real traversals once interpolated into a path -- ``<root>/..`` is the
parent of the root. Only *multi*-segment traversal (``a/../b``) was ever
blocked, and only incidentally, by the missing ``/``.

Two sites (``badges``, ``handback``) had already been patched by hand with an
explicit ``in (".", "..")`` check; the other five had not. These tests pin the
whole family to the one shared guard so the next copy-paste inherits it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = _WEB_SERVER.parent / "backend"
for _p in (str(_WEB_SERVER), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from server.routes import badges, handback, mcp_copilot, regression  # noqa: E402
from server.routes import tfactory_frameworks as frameworks  # noqa: E402
from server.routes import tfactory_tasks as tasks  # noqa: E402
from server.routes import tfactory_templates as templates  # noqa: E402
from server.routes._specpath import is_safe_slug, safe_slug  # noqa: E402

# ``.`` and ``..`` are the whole bug: single components that pass the charset
# and still traverse. The rest are the classic escapes, pinned so a future
# rewrite of the guard cannot quietly drop them.
TRAVERSALS = ["..", ".", "", "../etc", "a/b", "a/../../b", "/abs", "x\\y", "x\x00y"]


# ─── the shared guard itself ──────────────────────────────────────────────────


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_is_safe_slug_rejects_traversal(evil):
    assert is_safe_slug(evil) is False


@pytest.mark.parametrize("good", ["001-feature", "olafkfreund-TFactory", "a.b_c-1"])
def test_is_safe_slug_accepts_real_ids(good):
    assert is_safe_slug(good) is True


def test_safe_slug_returns_the_component_for_a_valid_id():
    assert safe_slug("001-feature", "nope") == "001-feature"


def test_safe_slug_raises_400_with_the_caller_s_detail():
    with pytest.raises(HTTPException) as exc:
        safe_slug("..", "invalid project_id")
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid project_id"


# ─── every route that takes an id and builds a path from it ───────────────────


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_regression_read_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        regression.get_regression_summary(evil)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_regression_run_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        regression.trigger_regression_run(evil, BackgroundTasks())
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_tfactory_tasks_spec_id_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        tasks._validate_spec_id(evil)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_tfactory_tasks_test_id_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        tasks._validate_test_id(evil)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_frameworks_name_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        frameworks._validate_name(evil)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_templates_segment_rejects_traversal(evil):
    with pytest.raises(HTTPException) as exc:
        templates._validate_segment(evil, "name")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_mcp_copilot_find_spec_dir_rejects_traversal(evil):
    # This one answers ``None`` rather than raising -- it is a lookup, not a
    # 4xx boundary -- but it must still refuse to look.
    assert mcp_copilot._find_spec_dir(evil) is None


# ─── the two that were already hand-patched: pin them ─────────────────────────


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_handback_ids_reject_traversal(evil):
    payload = handback.AIFactoryCompletePayload(project_id=evil, spec_id="001-x")
    with pytest.raises(HTTPException) as exc:
        handback._resolve_ids(payload)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("evil", TRAVERSALS)
def test_badges_serve_no_data_rather_than_traversing(evil):
    # Unauthenticated endpoint: a bad id gets the grey "no data" badge, not a
    # 4xx, so a README image never renders as a broken link.
    body = badges.test_acceptance_badge(evil, "001-x").body.decode()
    assert "no data" in body


# ─── reachable over HTTP, not just in-process ─────────────────────────────────


def test_percent_encoded_dotdot_reaches_the_handler_and_is_refused():
    """``%2E%2E`` survives routing as the literal id ``..``.

    A raw ``/api/projects/../regression`` is collapsed by the client before it
    is ever sent, which is why this looks unreachable until you encode it.
    Encoded, Starlette matches ``{project_id}`` on the raw path and unquotes
    afterwards, so the handler really does receive ``..``.
    """
    app = FastAPI()
    app.include_router(regression.router)
    resp = TestClient(app).get("/api/projects/%2E%2E/regression")
    assert resp.status_code == 400

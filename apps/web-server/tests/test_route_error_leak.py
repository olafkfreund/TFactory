"""CWE-209 on the route handlers (#1068, batch 2).

Twenty-two handlers across five route modules ended with:

    raise HTTPException(status_code=500, detail=f"...: {str(e)}")

and that string is a response body. An ``OSError`` renders the absolute path it
failed on, and several of these routes write ``.env`` — the path to a
credential store.

**Coverage, stated plainly.** These drive the settings and files routes through
a real ``TestClient``, which is five of the twenty-two. The other seventeen are
the identical single-expression conversion (``detail=f"ctx: {e}"`` ->
``detail=client_error(logger, "ctx", e)``) and depend on the same helper, whose
own behaviour is covered by ``test_error_ref.py``. This file is not claiming
route coverage of all twenty-two, and it should grow as the remaining batches
land.

Every case pins the status code. Without that, a request rejected at validation
returns 4xx before the handler runs and the leak assertion passes while testing
nothing — which is exactly how two of AIFactory#1301's first-draft tests were
hollow.

Mutation check: make ``server.error_ref.error_reference`` return ``str(exc)``
and every case below goes red naming the fragment that escaped.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.routes import files as files_routes, settings as settings_routes

CREDENTIALS_FILE = "/etc/tfactory/credentials.yaml"
INTERNAL_HOST = "tfactory-db.internal"
BOOM = ConnectionRefusedError(
    f"[Errno 111] Connection refused to {INTERNAL_HOST}:5432 "
    f"while reading {CREDENTIALS_FILE}"
)
LEAKS = (CREDENTIALS_FILE, INTERNAL_HOST, "Errno 111", "ConnectionRefusedError")


def _assert_no_leak(text: str) -> None:
    for fragment in LEAKS:
        assert fragment not in text, f"leaked {fragment!r} in response body: {text!r}"


@pytest.fixture
def settings_client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_routes.router, prefix="/api/settings")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path", "body", "expect"),
    [
        # sk-ant- prefix: the handler validates key FORMAT first and returns 400
        # without touching the disk, so a placeholder key would make this pass
        # without exercising anything.
        (
            "post",
            "/api/settings/api-key",
            {"keyType": "anthropic", "keyValue": "sk-ant-" + "x" * 40, "saveToEnv": True},
            500,
        ),
        ("put", "/api/settings/tab-state", {"tabs": ["a"]}, 500),
        ("patch", "/api/settings/source-env", {"DEBUG": "true"}, 500),
    ],
)
def test_a_disk_failure_does_not_put_our_paths_in_the_response(
    settings_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    expect: int,
) -> None:
    with (
        patch("pathlib.Path.write_text", side_effect=BOOM),
        patch("pathlib.Path.read_text", side_effect=BOOM),
    ):
        response = settings_client.request(method.upper(), path, json=body)

    assert response.status_code == expect, (
        f"{path} returned {response.status_code}; the handler was never reached, "
        f"so this assertion proves nothing: {response.text!r}"
    )
    _assert_no_leak(response.text)


def test_the_response_still_carries_something_to_quote(
    settings_client: TestClient,
) -> None:
    """Redaction must not reduce the caller to an opaque 500."""
    with (
        patch("pathlib.Path.write_text", side_effect=BOOM),
        patch("pathlib.Path.read_text", side_effect=BOOM),
    ):
        response = settings_client.put("/api/settings/tab-state", json={"tabs": ["a"]})

    assert "reference " in response.text, (
        f"no correlation id for the caller to quote: {response.text!r}"
    )


def test_a_files_write_failure_does_not_leak_the_path(tmp_path: Any) -> None:
    """files.py write/delete render an OSError naming a path under our root."""
    app = FastAPI()
    app.include_router(files_routes.router, prefix="/api/files")
    client = TestClient(app, raise_server_exceptions=False)

    # resolve_path is the seam this handler actually reads — patching
    # load_projects (which files.py never imports) fails with AttributeError,
    # and `path` is a QUERY parameter here, not part of the body.
    with (
        patch.object(files_routes, "resolve_path", return_value=tmp_path / "notes.txt"),
        patch("pathlib.Path.write_text", side_effect=BOOM),
    ):
        response = client.put(
            "/api/files/p1/write",
            params={"path": "notes.txt"},
            json={"content": "hello"},
        )

    assert response.status_code == 500, (
        f"handler not reached, assertion proves nothing: {response.text!r}"
    )
    _assert_no_leak(response.text)

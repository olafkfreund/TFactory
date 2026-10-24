"""Tests for the path-traversal sanitizer (CodeQL py/path-injection fix).

Spec ids arrive from request URLs; ``safe_spec_dir`` must build the specs path
for a legitimate id but reject any value that could escape the specs root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes._specpath import safe_spec_dir  # noqa: E402


def test_valid_spec_id_builds_expected_path(tmp_path):
    base = tmp_path / "proj"
    result = safe_spec_dir(base, "001-feature")
    assert result == base / ".tfactory" / "specs" / "001-feature"


@pytest.mark.parametrize(
    "evil",
    [
        "..",
        ".",
        "",
        "../etc",
        "../../etc/passwd",
        "a/b",
        "a/../../b",
        "/abs",
        "foo\\bar",
        "foo\x00bar",
    ],
)
def test_traversal_components_are_rejected(tmp_path, evil):
    with pytest.raises(HTTPException) as exc:
        safe_spec_dir(tmp_path / "proj", evil)
    assert exc.value.status_code == 400


def test_a_valid_but_absent_id_does_not_raise(tmp_path):
    # Only malicious components raise; a normal-but-nonexistent id returns the
    # path so callers can probe ``.exists()`` themselves.
    result = safe_spec_dir(tmp_path / "proj", "999-not-created-yet")
    assert result.name == "999-not-created-yet"
    assert not result.exists()


def test_result_stays_within_specs_root(tmp_path):
    base = tmp_path / "proj"
    root = (base / ".tfactory" / "specs").resolve()
    result = safe_spec_dir(base, "001-feature").resolve()
    assert root == result or root in result.parents


def test_trusted_project_root_resolves_existing_dir(tmp_path):
    from server.routes._specpath import trusted_project_root

    assert trusted_project_root(str(tmp_path)) == tmp_path.resolve()


def test_trusted_project_root_rejects_missing_dir(tmp_path):
    from server.routes._specpath import trusted_project_root

    with pytest.raises(ValueError, match="Project path does not exist"):
        trusted_project_root(str(tmp_path / "nope"))


def test_within_roots_inside_returns_resolved(tmp_path):
    from server.routes._specpath import within_roots

    assert within_roots(tmp_path / "a", [tmp_path], "x") == (tmp_path / "a").resolve()


@pytest.mark.parametrize("evil", ["/etc", "a/../../etc"])
def test_within_roots_outside_is_403_without_path(tmp_path, evil):
    from server.routes._specpath import within_roots

    cand = evil if evil.startswith("/") else str(tmp_path / evil)
    with pytest.raises(HTTPException) as exc:
        within_roots(cand, [tmp_path], "x")
    assert exc.value.status_code == 403
    assert "etc" not in exc.value.detail


def test_browse_roots_env_not_registry(tmp_path, monkeypatch):
    from server.routes._specpath import browse_roots

    monkeypatch.setenv("APP_FILE_BROWSE_ROOTS", str(tmp_path))
    monkeypatch.setattr(
        "server.routes.projects.load_projects",
        lambda: {"p": {"path": "/"}},
    )
    roots = browse_roots()
    assert tmp_path.resolve() in roots
    assert Path("/") not in roots  # a registered "/" must not widen the roots


def test_contained_path_unknown_user_is_403():
    from server.routes._specpath import within_roots

    with pytest.raises(HTTPException) as exc:
        within_roots("~nosuchuser_zz/x", [Path("/tmp")], "x", expand_user=True)
    assert exc.value.status_code == 403

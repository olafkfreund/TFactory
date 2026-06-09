#!/usr/bin/env python3
"""Tests for the PR status-check helper (WS1) — tools/pr_status.py.

Covers argv assembly (gh api statuses), dry-run (no subprocess), validation
(bad state / missing repo+sha), description truncation, and the real-run
success/failure paths via an injected runner_fn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tools.pr_status import (  # noqa: E402
    PRStatusRequest,
    post_pr_status,
)


def _req(tmp_path: Path, **kw) -> PRStatusRequest:
    base = dict(
        repo_dir=tmp_path,
        repo_slug="acme/widgets",
        sha="abc123",
        state="success",
        description="Gate passed: 3 accepted",
    )
    base.update(kw)
    return PRStatusRequest(**base)


def test_dry_run_builds_argv_no_subprocess(tmp_path):
    called = []

    def runner(argv, *, cwd, stdin=None):  # pragma: no cover - must not run
        called.append(argv)
        raise AssertionError("runner must not be invoked on dry-run")

    result = post_pr_status(_req(tmp_path), dry_run=True, runner_fn=runner)
    assert result.ok and result.dry_run
    assert called == []
    assert result.argv[:5] == ("gh", "api", "-X", "POST", "repos/acme/widgets/statuses/abc123")
    assert "-f" in result.argv and "state=success" in result.argv
    assert "context=TFactory / tests" in result.argv


def test_invalid_state_rejected(tmp_path):
    result = post_pr_status(_req(tmp_path, state="green"), dry_run=True)
    assert result.ok is False
    assert "invalid state" in result.error


def test_missing_repo_or_sha_rejected(tmp_path):
    assert post_pr_status(_req(tmp_path, repo_slug=""), dry_run=True).ok is False
    assert post_pr_status(_req(tmp_path, sha=""), dry_run=True).ok is False


def test_description_truncated_to_140(tmp_path):
    long_desc = "x" * 200
    result = post_pr_status(_req(tmp_path, description=long_desc), dry_run=True)
    desc_arg = next(a for a in result.argv if a.startswith("description="))
    assert len(desc_arg) == len("description=") + 140


def test_target_url_included_when_present(tmp_path):
    result = post_pr_status(
        _req(tmp_path, target_url="https://x/report"), dry_run=True
    )
    assert any(a == "target_url=https://x/report" for a in result.argv)


def test_target_url_omitted_when_empty(tmp_path):
    result = post_pr_status(_req(tmp_path, target_url=""), dry_run=True)
    assert not any(a.startswith("target_url=") for a in result.argv)


def test_real_run_success(tmp_path):
    class _Res:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def runner(argv, *, cwd, stdin=None):
        assert stdin is None
        return _Res()

    result = post_pr_status(_req(tmp_path), dry_run=False, runner_fn=runner)
    assert result.ok is True and result.dry_run is False


def test_real_run_failure_surfaces_stderr(tmp_path):
    class _Res:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403: resource not accessible"

    result = post_pr_status(
        _req(tmp_path), dry_run=False, runner_fn=lambda *a, **k: _Res()
    )
    assert result.ok is False
    assert "403" in result.error


def test_real_run_missing_repo_dir(tmp_path):
    result = post_pr_status(
        _req(tmp_path / "missing"), dry_run=False, runner_fn=lambda *a, **k: None
    )
    assert result.ok is False
    assert "does not exist" in result.error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

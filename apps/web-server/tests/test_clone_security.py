"""Security regression tests for the workspace clone path (Factory review C1/H1).

C1: a malicious git URL (``ext::sh -c ...``) must be refused before any git op,
and git's transports are restricted via ``GIT_ALLOW_PROTOCOL``.
H1: a credential must never appear in the git argv (it travels via GIT_ASKPASS).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services import project_workspace_service as pws  # noqa: E402


@pytest.mark.parametrize(
    "evil",
    [
        "ext::sh -c id",
        'ext::sh -c "id"',
        "fd::17/foo",
        "-oProxyCommand=id",
        "file:///etc/passwd",
        "",
    ],
)
def test_malicious_clone_urls_rejected(evil):
    with pytest.raises(pws.GitOperationError):
        pws.validate_git_url(evil)


@pytest.mark.parametrize(
    "ok",
    [
        "https://github.com/o/r.git",
        "http://host/o/r",
        "ssh://git@host/o/r",
        "git@github.com:o/r.git",
        "git://host/o/r",
    ],
)
def test_legitimate_clone_urls_pass(ok):
    assert pws.validate_git_url(ok) == ok


def _capture_git_calls(tmp_path):
    """Patch the subprocess so we capture every git argv + env, without running git."""
    calls: list[dict] = []

    async def fake_exec(*cmd, **kwargs):
        calls.append({"cmd": list(cmd), "env": kwargs.get("env") or {}})
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    return calls, fake_exec


def test_credential_never_in_git_argv(tmp_path):
    calls, fake_exec = _capture_git_calls(tmp_path)
    token = "ghp_SUPERSECRETTOKEN1234567890"
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        import asyncio

        asyncio.run(
            pws.clone_or_update(
                "https://github.com/owner/repo.git",
                root=tmp_path,
                credential=("oauth2", token),
            )
        )
    assert calls, "expected at least one git invocation"
    for c in calls:
        flat = " ".join(c["cmd"])
        assert token not in flat, f"token leaked into argv: {flat}"
        # C1: transports restricted on every git call.
        assert c["env"].get("GIT_ALLOW_PROTOCOL") == "https:http:ssh:git:file"
    # H1: the credentialed clone delivered the token via GIT_ASKPASS env, and
    # the clone URL carried only the username.
    clone = next(c for c in calls if "clone" in c["cmd"])
    assert clone["env"].get("GIT_PASS") == token
    assert any("oauth2@github.com" in a for a in clone["cmd"])

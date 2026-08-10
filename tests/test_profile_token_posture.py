"""Claude/API profile at-rest posture — #663 (CodeQL #711).

Profiles hold OAuth tokens and are stored as plaintext JSON at 0600 by design
(the rationale lives on ``settings.save_profiles``). What that posture *does*
promise is that the token is never readable by anyone but the owning uid — so
these tests pin the property that the 0600 is real, including during the write
itself, which ``write_text``-then-``chmod`` did not guarantee.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

import pytest  # noqa: E402
from server import paths  # noqa: E402
from server.routes import settings as settings_routes  # noqa: E402
from server.routes import settings_api_profiles  # noqa: E402


@pytest.fixture
def permissive_umask():
    """Mask nothing off, so a mode bug shows up instead of being hidden."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


def test_write_secret_file_never_leaves_a_readable_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permissive_umask: None
) -> None:
    """The file is 0600 *while* the secret is being written, not just after.

    ``write_text`` + ``chmod`` creates at 0644 under this umask and only narrows
    afterwards; the token is world-readable in between. Observe the mode from
    inside the write itself to prove that window is gone.
    """
    target = tmp_path / "claude-profiles.json"
    modes_during_write: list[int] = []
    real_write = os.write

    def spy(fd: int, data: bytes) -> int:
        modes_during_write.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_write(fd, data)

    monkeypatch.setattr(paths.os, "write", spy)
    paths.write_secret_file(target, '{"oauthToken": "sk-ant-oat01-test"}')

    assert modes_during_write == [0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_secret_file_repairs_an_existing_loose_mode(
    tmp_path: Path, permissive_umask: None
) -> None:
    """O_CREAT's mode is ignored for an existing file — the chmod must still run.

    Covers a profiles file left at 0644 by an older build or a restored backup.
    """
    target = tmp_path / "claude-profiles.json"
    target.write_text("{}")
    target.chmod(0o644)

    paths.write_secret_file(target, '{"oauthToken": "sk-ant-oat01-test"}')

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_secret_file_truncates_stale_content(tmp_path: Path) -> None:
    """A shorter write must not leave a tail of the previous token behind."""
    target = tmp_path / "claude-profiles.json"
    paths.write_secret_file(target, '{"oauthToken": "sk-ant-oat01-a-long-old-token"}')
    paths.write_secret_file(target, "{}")

    assert target.read_text() == "{}"


def test_save_profiles_writes_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permissive_umask: None
) -> None:
    """The CodeQL #711 site itself: tokens land at 0600, never group/world-readable."""
    target = tmp_path / "claude-profiles.json"
    monkeypatch.setattr(settings_routes, "get_profiles_file", lambda: target)

    settings_routes.save_profiles(
        {
            "profiles": [{"id": "p1", "oauthToken": "sk-ant-oat01-test"}],
            "activeProfileId": "p1",
        }
    )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_save_api_profiles_writes_0600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permissive_umask: None
) -> None:
    """API profiles carry keys too and share the posture."""
    target = tmp_path / "api-profiles.json"
    monkeypatch.setattr(settings_api_profiles, "get_api_profiles_file", lambda: target)

    settings_api_profiles.save_api_profiles(
        {"profiles": [{"id": "a1", "apiKey": "test-key"}], "activeProfileId": "a1"}
    )

    assert stat.S_IMODE(target.stat().st_mode) == 0o600

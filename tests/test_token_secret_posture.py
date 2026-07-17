"""The API token and JWT secret are 0600 from creation (#663 follow-up).

#663 routed the Claude *profile* writers through ``write_secret_file``, but the
API auth token (``.token``) and the JWT signing secret (``.jwt_secret``) still
did ``write_text`` then ``chmod`` — which creates at the umask default (0644)
and leaves the secret world-readable for the duration of the write. These are
at least as sensitive as the profile tokens.

The mode is observed with ``os.fstat`` on the fd *during* the write, under
``umask(0)`` so the bug surfaces instead of being masked by a strict umask.
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
from server.paths import write_secret_file  # noqa: E402


@pytest.fixture
def permissive_umask():
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


def _mode_during_write(path: Path, text: str) -> int:
    """Capture the file mode as seen from INSIDE the write, not after."""
    seen: dict[str, int] = {}
    real_write = os.write

    def spy(fd: int, data: bytes) -> int:
        seen.setdefault("mode", stat.S_IMODE(os.fstat(fd).st_mode))
        return real_write(fd, data)

    os.write = spy  # type: ignore[assignment]
    try:
        write_secret_file(path, text)
    finally:
        os.write = real_write  # type: ignore[assignment]
    if "mode" not in seen:
        # Nothing reached os.write, so the secret was written through the
        # buffered IO layer (Path.write_text) — i.e. created at the umask
        # default and only narrowed by a later chmod. That is precisely the
        # world-readable window this test exists to forbid.
        pytest.fail(
            "secret was not written via os.open/os.write with an explicit mode; "
            "a write_text-then-chmod path leaves it world-readable mid-write"
        )
    return seen["mode"]


def test_secret_never_world_readable_during_write(
    tmp_path: Path, permissive_umask: None
) -> None:
    """The old write_text-then-chmod left 0644 mid-write; this pins 0600."""
    p = tmp_path / ".token"
    assert _mode_during_write(p, "sekrit") == 0o600
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_existing_loose_mode_is_repaired(
    tmp_path: Path, permissive_umask: None
) -> None:
    """O_CREAT's mode is ignored for an existing file — the trailing chmod repairs it."""
    p = tmp_path / ".jwt_secret"
    p.write_text("old")
    p.chmod(0o644)
    write_secret_file(p, "new")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_text() == "new"


def test_content_is_truncated_not_appended(tmp_path: Path) -> None:
    p = tmp_path / ".token"
    write_secret_file(p, "a-long-old-token")
    write_secret_file(p, "short")
    assert p.read_text() == "short"

"""First-run setup must never print the API token value to stdout.

TFactory config.py printed ``Generated API token: {token}`` and
``Authorization: Bearer {token}`` on first boot with no ``APP_API_TOKEN``.
Those lines land in the pod log, journald and any CI job that boots the server —
anyone with ``kubectl logs`` gets the wildcard admin credential. AIFactory
already fixed this (#324 M1); this is the fork-drift port.

The assertions are on the captured stdout LINES, matched against the token VALUE
itself, so they cannot be satisfied by the path-only replacement message.
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
from server import config as config_mod  # noqa: E402


@pytest.fixture
def permissive_umask():
    """umask 0, so a 0644-creating write is not masked into looking safe."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


def _generate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(config_mod, "get_data_file", lambda name: tmp_path / name)
    settings = config_mod.Settings.__new__(config_mod.Settings)
    return config_mod.Settings._get_or_generate_token(settings)


def test_first_run_never_prints_the_token_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _generate(tmp_path, monkeypatch)
    lines = capsys.readouterr().out.splitlines()

    leaked = [line for line in lines if token in line]
    assert not leaked, (
        "the API token value was printed to stdout (pod log / journald / CI log): "
        f"{leaked}"
    )


def test_first_run_still_tells_the_operator_where_the_token_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usability half of the fix: the path must still be discoverable."""
    _generate(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    token_file = tmp_path / ".token"

    assert str(token_file) in out, out
    assert f"cat {token_file}" in out, out

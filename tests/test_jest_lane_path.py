"""The jest runner must be on PATH inside the nix shell (#1195).

`npm install -g` lands the binary under `npm prefix -g`, whose bin dir the
nix develop shell does not put on PATH. The install SUCCEEDED and `jest` was
still not found -- so the loud failure guard never fired and every unit test
reported `consistent_fail` with a bare "jest: command not found" on the first
rerun line, which reads as the test's own failure.
"""

from __future__ import annotations

import inspect

from agents import nix_env


def _lane_source() -> str:
    return inspect.getsource(nix_env.run_jest_lane_via_nix)


def test_the_npm_global_bin_is_prepended_to_path():
    src = _lane_source()
    assert "npm prefix -g" in src, "npm global bin never added to PATH"
    assert "export PATH=" in src


def test_path_export_precedes_the_jest_invocation():
    """Exporting after the runs would be useless -- order is the whole point."""
    src = _lane_source()
    assert src.index("export PATH=") < src.index("jest --ci"), (
        "PATH is exported after jest is invoked"
    )


def test_a_path_miss_fails_loudly_rather_than_as_a_test_failure():
    src = _lane_source()
    assert "jest installed but not on PATH" in src, (
        "no assertion that the runner is callable; a PATH miss would again be "
        "misattributed to the test"
    )

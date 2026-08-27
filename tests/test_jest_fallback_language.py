"""A jest lane must not provision a python environment.

The provisioner treats an UNSET language as python -- deliberately, so a
manifest that omits it still gets the pytest harness. `_JEST_FALLBACK_ENV`
declared no language, so every jest lane running without a contract env
provisioned a python env it can never use. Building that env inside the verify
Job's cgroup is what produced

    error: Cannot build '/nix/store/...-python3-3.13.13-env.drv'.
           Reason: builder failed due to signal 9 (Killed).

in specs 190 and 192, costing a jest verdict each time.
"""

from __future__ import annotations

import re

from agents.nix_env import _BROWSER_FALLBACK_ENV, _JEST_FALLBACK_ENV
from tools.runners.nix_provisioner import generate_flake

_LIBS = re.compile(r"withPackages \(p: \[([^\]]*)\]")


def _python_libs(env: dict) -> str:
    m = _LIBS.search(generate_flake(dict(env)))
    return m.group(1).strip() if m else ""


def test_the_jest_fallback_declares_its_language():
    assert _JEST_FALLBACK_ENV.get("language") == "javascript"


def test_a_jest_lane_provisions_no_python_at_all():
    assert _python_libs(_JEST_FALLBACK_ENV) == "", (
        "a jest lane cannot use a python env; building one is what OOM-killed the Job"
    )


def test_the_jest_fallback_still_asks_for_the_runner():
    """The language must not displace the token the provisioner keys on."""
    assert "jest" in _JEST_FALLBACK_ENV["system_packages"]
    assert "nodejs" in generate_flake(dict(_JEST_FALLBACK_ENV))


def test_the_browser_fallback_is_deliberately_left_alone():
    """A browser lane says nothing about the language -- a PYTHON web app with
    one, and no contract env, would be mislabelled and lose its harness. Pinned
    so the asymmetry is a decision on record rather than an oversight."""
    assert "language" not in _BROWSER_FALLBACK_ENV

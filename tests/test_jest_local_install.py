"""The jest runner must be installed LOCALLY, not globally (#1195).

`npm install -g jest ts-jest` gives each package its own tree. ts-jest landed
at the top level while its peer jest-util was nested under jest/node_modules/,
where ts-jest could not resolve it:

    Error: Cannot find module 'jest-util'
    Require stack:
    - .../node_modules/ts-jest/dist/legacy/config/config-set.js
    - .../node_modules/jest/node_modules/jest-util/build/index.js

A local install hoists one flat tree, making them siblings.
"""

from __future__ import annotations

import inspect

from agents import nix_env


def _src() -> str:
    return inspect.getsource(nix_env.run_jest_lane_via_nix)


def test_the_runner_is_not_installed_globally():
    assert "npm install -g" not in _src(), (
        "a global install renests ts-jest's peers out of its resolution path"
    )


def test_the_install_is_local():
    src = _src()
    assert "npm install --no-save" in src


def test_the_binary_is_invoked_by_a_fixed_local_path():
    """No PATH or NODE_PATH dependency -- both were workarounds for -g."""
    assert nix_env._JEST_BIN == "./node_modules/.bin/jest"
    src = _src()
    assert "_JEST_BIN}" in src, "the run command does not use the local binary"
    assert "NODE_PATH=" not in src


def test_a_missing_runner_still_fails_loudly():
    assert "__JEST_SETUP_FAILED" in _src()

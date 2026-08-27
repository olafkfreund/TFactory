"""Generated JS/TS tests must import a path that resolves (#1195).

With the toolchain finally working (jest runs, ts-jest transforms), spec 188
showed the real remaining blocker:

    Cannot find module 'app/games/tictactoe/game'
    > 5 | import { emptyBoard, move } from "app/games/tictactoe/game";

while the module actually lives at `<rootDir>/games/tictactoe/game.js`.

Cause: gen_functional.md told the model to use "the dotted path matching the
project layout -- NOT relative imports". That is Python-only guidance; in
JS/TS it forbids the one form that resolves, and a bare specifier is a
node_modules lookup rather than a project-root path.
"""

from __future__ import annotations

from pathlib import Path

from agents.nix_env import _write_jest_config

_PROMPT = Path(__file__).resolve().parents[1] / "apps/backend/prompts/gen_functional.md"


def test_the_prompt_no_longer_forbids_relative_imports_for_js():
    body = _PROMPT.read_text()
    assert "JavaScript / TypeScript" in body, "import guidance is not language-aware"
    assert "a RELATIVE path from the test file" in body


def test_the_prompt_warns_that_a_bare_specifier_is_a_node_modules_lookup():
    assert "node_modules lookup" in _PROMPT.read_text()


def test_the_config_maps_a_bare_app_prefix_when_no_app_dir_exists(tmp_path):
    (tmp_path / "games").mkdir()

    cfg = _write_jest_config(tmp_path).read_text()

    assert "moduleNameMapper" in cfg
    assert "'^app/(.*)$': '<rootDir>/$1'" in cfg


def test_a_project_that_really_has_an_app_dir_is_not_remapped(tmp_path):
    """Remapping a real `app/` would break a project that legitimately ships one."""
    (tmp_path / "app").mkdir()

    cfg = _write_jest_config(tmp_path).read_text()

    # The mapper itself still exists -- it carries the unconditional .worktree
    # entry -- so assert the absence of the APP rule specifically, not of the
    # whole block.
    assert "'^app/(.*)$'" not in cfg
    assert ".worktree" in cfg


def test_the_transform_is_still_configured(tmp_path):
    cfg = _write_jest_config(tmp_path).read_text()

    assert "ts-jest" in cfg


def test_the_config_maps_a_worktree_prefixed_path(tmp_path):
    """Observed in spec 189: the generator spliced the checkout directory's own
    name into a relative import --

        Cannot find module '../../.worktree/games/tictactoe/game.js'

    -- while the test file was already inside that checkout. The worktree IS
    rootDir, so anything routed through it belongs at rootDir.
    """
    (tmp_path / "games").mkdir()

    cfg = _write_jest_config(tmp_path).read_text()

    assert ".worktree" in cfg
    assert "<rootDir>/$1" in cfg


def test_the_prompt_warns_against_naming_the_checkout_directory():
    assert "ALREADY INSIDE the project checkout" in _PROMPT.read_text()

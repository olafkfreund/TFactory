"""A generated test that imports a path which does not exist is a generation bug.

Spec 182: five jest tests imported `app/games/tictactoe/game` while the module
sits at `games/tictactoe/game.js`. Nothing checked it -- the test was written,
ran, failed to import, and the evaluator recorded `flaky`/`consistent_fail`.
"""

from __future__ import annotations

from agents.gen_functional import _source_guardrail_rejection, _unresolvable_imports


class _St:
    """Non-python subtask: the python-only guards above must not fire."""

    language = "typescript"
    framework = "jest"
    acceptance_criterion = ""


def _project(tmp_path):
    (tmp_path / "games" / "tictactoe").mkdir(parents=True)
    (tmp_path / "games" / "tictactoe" / "game.js").write_text("export const x=1;")
    (tmp_path / "node_modules" / "lodash").mkdir(parents=True)
    return tmp_path


def test_the_invented_app_prefix_is_caught(tmp_path):
    """Verbatim from spec 182's generated tests."""
    src = 'import { move } from "app/games/tictactoe/game";'
    assert _unresolvable_imports(src, _project(tmp_path)) == [
        "app/games/tictactoe/game"
    ]


def test_the_real_path_resolves(tmp_path):
    src = 'import { move } from "games/tictactoe/game";'
    assert _unresolvable_imports(src, _project(tmp_path)) == []


def test_extensioned_real_path_resolves(tmp_path):
    src = 'import { move } from "games/tictactoe/game.js";'
    assert _unresolvable_imports(src, _project(tmp_path)) == []


def test_packages_are_not_flagged(tmp_path):
    """@scope, node:, bare names and node_modules paths must all pass."""
    src = (
        'import { test } from "@playwright/test";\n'
        'import path from "node:path";\n'
        'import merge from "lodash/merge";\n'
        'import fs from "fs";\n'
        'import { a } from "./sibling";\n'
    )
    assert _unresolvable_imports(src, _project(tmp_path)) == []


def test_the_guardrail_rejects_with_its_own_reason(tmp_path):
    """It must not surface later as generic flakiness."""
    src = 'import { move } from "app/games/tictactoe/game";'
    got = _source_guardrail_rejection(_St(), src, _project(tmp_path))
    assert got is not None
    reason, phase = got
    assert phase == "gen_functional_unresolvable_import"
    assert "app/games/tictactoe/game" in reason


def test_a_clean_typescript_test_is_not_rejected(tmp_path):
    src = 'import { move } from "games/tictactoe/game";\ntest("x", () => {});'
    assert _source_guardrail_rejection(_St(), src, _project(tmp_path)) is None

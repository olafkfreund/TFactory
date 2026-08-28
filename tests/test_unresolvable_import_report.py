"""A generated test importing a path that does not exist must stay VISIBLE.

Spec 182: five jest tests imported `app/games/tictactoe/game` while the module
sits at `games/tictactoe/game.js`. #1192 made that a hard guardrail and #1194
reverted it -- the rejection triggered a Planner replan storm. What replaced it
was a jest `moduleNameMapper` rewriting `^app/(.*)$` to `<rootDir>`, so the same
wrong import now passes in the jest lane and still breaks everywhere else: the
defect went from loudly wrong to silently masked (TFactory#1174).

So the contract under test is REPORTING, not rejection: the specifier must be
named in the spec's status.json. These tests fail if it is silently rewritten.
"""

from __future__ import annotations

import json

from agents.gen_functional import (
    _record_unresolvable_imports,
    _source_guardrail_rejection,
    _unresolvable_imports,
)


class _St:
    """Non-python subtask: the python-only guards must not fire on it."""

    id = "st-1"
    language = "typescript"
    framework = "jest"
    acceptance_criterion = ""


def _project(tmp_path):
    pd = tmp_path / "project"
    (pd / "games" / "tictactoe").mkdir(parents=True)
    (pd / "games" / "tictactoe" / "game.js").write_text("export const x=1;")
    (pd / "node_modules" / "lodash").mkdir(parents=True)
    return pd


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
    )
    assert _unresolvable_imports(src, _project(tmp_path)) == []


def test_a_relative_import_is_judged_from_the_test_file(tmp_path):
    """The mapper's other rule redirects `../../.worktree/...`; catch it too."""
    pd = _project(tmp_path)
    tests_dir = pd / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "game.test.ts"
    good = 'import { move } from "../games/tictactoe/game";'
    assert _unresolvable_imports(good, pd, test_file) == []
    bad = 'import { move } from "../../.worktree/games/tictactoe/game";'
    assert _unresolvable_imports(bad, pd, test_file) == [
        "../../.worktree/games/tictactoe/game"
    ]


def test_python_source_is_never_scanned(tmp_path):
    """The regex matches QUOTED specifiers, so a Python import cannot trip it."""
    src = "from app.games.tictactoe import game\nimport app.main\n"
    assert _unresolvable_imports(src, _project(tmp_path)) == []


def test_the_bad_import_is_recorded_not_rejected(tmp_path):
    """The whole point of #1174: visible, and NOT routed to a replan.

    A mapper that silently rewrites `app/...` to `<rootDir>` leaves status.json
    untouched -- so this assertion is exactly what masking would break.
    """
    pd = _project(tmp_path)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    src = 'import { move } from "app/games/tictactoe/game";'
    test_file = pd / "tests" / "game.test.ts"
    test_file.parent.mkdir()
    test_file.write_text(src)

    assert _record_unresolvable_imports(spec_dir, _St(), src, pd, test_file) == [
        "app/games/tictactoe/game"
    ]
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["unresolvable_imports"] == [
        "st-1: unresolvable import 'app/games/tictactoe/game'"
    ]
    assert status["gen_functional_warnings"] == status["unresolvable_imports"]
    # Recorded, never rejected -- #1194 reverted rejection for the replan storm.
    assert _source_guardrail_rejection(_St(), src, pd) is None


def test_a_second_subtask_does_not_clobber_the_first(tmp_path):
    pd = _project(tmp_path)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    test_file = pd / "tests" / "game.test.ts"
    test_file.parent.mkdir()
    for name in ("app/a", "app/b"):
        _record_unresolvable_imports(
            spec_dir, _St(), f'import x from "{name}";', pd, test_file
        )
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["unresolvable_imports"] == [
        "st-1: unresolvable import 'app/a'",
        "st-1: unresolvable import 'app/b'",
    ]


def test_a_clean_test_records_nothing(tmp_path):
    pd = _project(tmp_path)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    test_file = pd / "tests" / "game.test.ts"
    test_file.parent.mkdir()
    src = 'import { move } from "games/tictactoe/game";'
    assert _record_unresolvable_imports(spec_dir, _St(), src, pd, test_file) == []
    assert not (spec_dir / "status.json").exists()

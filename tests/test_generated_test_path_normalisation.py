"""Generated tests must land under `<spec_dir>/tests/`.

Spec 172 produced ZERO browser evidence -- 7 planned playwright subtasks, all 7
files written, `browser evidence: ok=False screenshots=0` -- because the planner
emitted `games/tictactoe/tests/e2e/*.spec.ts` and `_stage_browser_specs` globs
`<spec_dir>/tests` exactly. `files_to_create` is whatever the planner LLM typed;
nothing validated it.
"""

from __future__ import annotations

import json

from agents import nix_env
from agents.gen_functional import _files_to_create, _normalise_test_path


def test_a_nested_tests_dir_is_rebased():
    """The spec-172 case, verbatim from its test_plan.json."""
    assert (
        _normalise_test_path("games/tictactoe/tests/e2e/x.spec.ts", "playwright")
        == "tests/e2e/x.spec.ts"
    )


def test_a_bare_e2e_dir_is_rebased():
    """frameworks/playwright/descriptor.yaml also legitimised `e2e/**`."""
    assert _normalise_test_path("e2e/x.spec.ts", "playwright") == "tests/e2e/x.spec.ts"


def test_an_already_correct_path_is_untouched():
    assert (
        _normalise_test_path("tests/e2e/x.spec.ts", "playwright")
        == "tests/e2e/x.spec.ts"
    )


def test_go_tests_are_left_beside_their_package():
    """Go REQUIRES `_test.go` in the package it tests -- rebasing breaks the build."""
    assert (
        _normalise_test_path("pkg/game/game_test.go", "go-test")
        == "pkg/game/game_test.go"
    )


def test_the_getter_normalises_for_both_the_prompt_and_the_write_path():
    """`_files_to_create` feeds the prompt AND the `spec_dir / files[0]` check."""
    st = {
        "framework": "playwright",
        "files_to_create": ["games/tictactoe/tests/e2e/x.spec.ts"],
    }
    assert _files_to_create(st) == ["tests/e2e/x.spec.ts"]


def test_the_browser_lane_stages_a_normalised_spec(tmp_path, monkeypatch):
    """End of the chain: a spec at the normalised path is actually staged.

    Exercises run_browser_evidence rather than asserting file layout -- a hollow
    test in this area passed with the bug present.
    """
    spec_dir = tmp_path / "spec"
    (spec_dir / "context").mkdir(parents=True)
    (spec_dir / "context" / "source.json").write_text(json.dumps({"target_paths": []}))

    planned = _files_to_create(
        {
            "framework": "playwright",
            "files_to_create": ["games/tictactoe/tests/e2e/x.spec.ts"],
        }
    )[0]
    dest = spec_dir / planned
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("// generated spec")

    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    class _Res:
        returncode = 0
        stdout = ""

    class _Sandbox:
        def run(self, *a, **k):
            return _Res()

    monkeypatch.setattr(nix_env, "materialize_flake", lambda *a, **k: object())
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: _Sandbox())
    monkeypatch.setattr(nix_env, "_write_pw_config", lambda *a, **k: None)
    monkeypatch.setattr(nix_env, "detect_serve_command", lambda *a, **k: None)

    res = nix_env.run_browser_evidence(spec_dir, project_dir)
    assert res is not None
    assert res.get("specs") == 1, (
        f"browser lane staged nothing: {res.get('output_tail')!r}"
    )

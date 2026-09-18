"""Generated tests must not ship imports that resolve to nothing (#1174).

Specs 182/193-195 wrote `app/games/tictactoe/game` for a module at
`games/tictactoe/game.js`. #1233 made that visible (report-only) after #1192's
rejection was reverted (#1194: every rejection cost a full Planner replan).
Now it is fixed inside Gen-Functional: a free deterministic rewrite when exactly
one real module matches, else one bounded retry with feedback — and never a
replan. Real orchestration (``run_gen_functional``) with the SDK faked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.gen_functional import run_gen_functional

_TEST_REL = "tests/unit/game.test.js"


@pytest.fixture(autouse=True)
def _no_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("TFACTORY_AUTO_EVALUATE", "0")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pd = tmp_path / "project"
    (pd / "games" / "tictactoe").mkdir(parents=True)
    (pd / "games" / "tictactoe" / "game.js").write_text("export const move = 1;\n")
    return pd


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001"
    for sub in ("context", "tests", "findings", "logs"):
        (d / sub).mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"status": "planned"}))
    subtask = {
        "id": "s0",
        "description": "move places a mark",
        "status": "pending",
        "lane": "unit",
        "language": "javascript",
        "framework": "jest",
        "target": "games/tictactoe/game.js::move",
        "rationale": "AC#1",
        "files_to_create": [_TEST_REL],
    }
    (d / "test_plan.json").write_text(
        json.dumps(
            {
                "feature": "demo",
                "workflow_type": "feature",
                "phases": [{"phase": 1, "name": "AC#1", "subtasks": [subtask]}],
                "status": "in_progress",
            }
        )
    )
    return d


def _js(specifier: str) -> str:
    return (
        f'import {{ move }} from "{specifier}";\n'
        'test("move", () => {\n  expect(move).toBe(1);\n});\n'
    )


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch):
    """Fake the two SDK seams; each session writes the next source in turn."""
    prompts: list[str] = []

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    def _setup(*sources: str) -> list[str]:
        queue = list(sources)

        async def _resolve(*a, **kw):
            return _CM()

        async def _invoke(client, prompt, spec_dir, verbose):
            prompts.append(prompt)
            src = queue.pop(0) if len(queue) > 1 else queue[0]
            for line in prompt.splitlines():
                if line.startswith("- write the file at:"):
                    path = Path(line.split("`")[1])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(src)
                    break
            return "complete", "ok", {}

        monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
        monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)
        return prompts

    return _setup


def _status(spec: Path) -> dict:
    return json.loads((spec / "status.json").read_text())


async def test_a_correct_relative_import_is_judged_from_where_the_test_runs(
    spec: Path, project: Path, sdk
) -> None:
    """The test runs staged at its authored path INSIDE the project worktree, so
    `../../games/...` is right. Resolving it from the spec dir reported it."""
    sdk(_js("../../games/tictactoe/game"))

    assert await run_gen_functional(spec, project) is True
    assert "unresolvable_imports" not in _status(spec)


async def test_an_unambiguous_bad_prefix_is_rewritten_for_free(
    spec: Path, project: Path, sdk
) -> None:
    prompts = sdk(_js("app/games/tictactoe/game"))

    assert await run_gen_functional(spec, project) is True

    written = (spec / _TEST_REL).read_text()
    assert '"../../games/tictactoe/game"' in written
    assert "app/games" not in written
    assert len(prompts) == 1  # no LLM call spent on it
    status = _status(spec)
    assert "unresolvable_imports" not in status
    assert status["import_rewrites"] == [
        "s0: 'app/games/tictactoe/game' -> '../../games/tictactoe/game'"
    ]


async def test_two_candidate_modules_are_never_guessed_between(
    spec: Path, project: Path, sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`app/tictactoe/game` could be games/tictactoe/game or tictactoe/game."""
    monkeypatch.setenv("TFACTORY_GEN_IMPORT_RETRIES", "0")
    (project / "tictactoe").mkdir()
    (project / "tictactoe" / "game.js").write_text("export const move = 2;\n")
    sdk(_js("app/x/tictactoe/game"))
    (project / "x").mkdir()
    (project / "x" / "tictactoe").mkdir()
    (project / "x" / "tictactoe" / "game.js").write_text("export const move = 3;\n")

    assert await run_gen_functional(spec, project) is True

    assert "app/x/tictactoe/game" in (spec / _TEST_REL).read_text()
    status = _status(spec)
    assert "import_rewrites" not in status
    assert status["unresolvable_imports"] == [
        "s0: unresolvable import 'app/x/tictactoe/game'"
    ]


async def test_a_still_bad_import_gets_one_retry_and_never_a_replan(
    spec: Path, project: Path, sdk
) -> None:
    prompts = sdk(_js("app/nothing/here"))

    assert await run_gen_functional(spec, project) is True

    assert len(prompts) == 2  # the original + exactly one retry
    assert "app/nothing/here" in prompts[1]  # the retry names what is wrong
    status = _status(spec)
    assert status["status"] != "replan_needed"
    assert not (spec / "context" / "replan_request.json").exists()
    assert status["import_retries"] == ["s0: retry 1 for 'app/nothing/here'"]
    assert status["unresolvable_imports"] == [
        "s0: unresolvable import 'app/nothing/here'"
    ]


async def test_a_retry_that_fixes_it_ships_clean(
    spec: Path, project: Path, sdk
) -> None:
    prompts = sdk(_js("app/nothing/here"), _js("../../games/tictactoe/game"))

    assert await run_gen_functional(spec, project) is True

    assert len(prompts) == 2
    assert "unresolvable_imports" not in _status(spec)


async def test_a_zero_budget_means_no_retry(
    spec: Path, project: Path, sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TFACTORY_GEN_IMPORT_RETRIES", "0")
    prompts = sdk(_js("app/nothing/here"))

    assert await run_gen_functional(spec, project) is True

    assert len(prompts) == 1
    assert "import_retries" not in _status(spec)


def test_the_verdict_names_the_unresolvable_import(spec: Path) -> None:
    """Not a generic flaky/consistent_fail: the cause, measured, tagged system."""
    from agents.confidence import reason_lines
    from agents.evaluator import _stamp_unresolvable_import_reasons

    status = _status(spec)
    status["unresolvable_imports"] = ["s0: unresolvable import 'app/nothing/here'"]
    (spec / "status.json").write_text(json.dumps(status))
    doc = {
        "verdicts": [
            {
                "test_id": "a paraphrase",
                "test_file": _TEST_REL,
                "verdict": "reject",
                "reasons": ["judge: looks flaky"],
            }
        ]
    }

    assert _stamp_unresolvable_import_reasons(spec, doc) == 1

    lines = reason_lines(doc["verdicts"][0])
    assert lines[0] == ("judge: looks flaky", "model")
    assert lines[1][1] == "system"
    assert "unresolvable import 'app/nothing/here'" in lines[1][0]
    assert doc["verdicts"][0]["verdict"] == "reject"  # category unchanged

"""A review gate that cannot read its input must hold the task, not release it.

TFactory#1139. `start_task_execution` decides whether a task needs human review
before coding:

    require_review = False                          # default
    ...
    try:
        task_metadata = json.loads(task_metadata_file.read_text())
        require_review = task_metadata.get("requireReviewBeforeCoding", False)
    except (json.JSONDecodeError, OSError):
        pass                                        # <-- the defect
    ...
    if not require_review or force:
        cmd.append("--force")                       # bypass the review gate

A truncated or unreadable `task_metadata.json` was swallowed, `require_review`
kept its `False` default, and a task whose author explicitly asked for review
went straight to coding. `except: pass` there does not mean "no review
requested" -- it means "I could not find out whether review was requested", and
only one of those is safe to treat as False.

BOTH handlers had it. The sync-path one logged a warning, which made it look
sound; a log line does not change the decision, so it failed open just as the
silent one did.

Asserted structurally rather than behaviourally on purpose. The gate lives 40
lines into a 420-line function that spawns subprocesses, so a behavioural test
would need enough mocking to be its own liability -- and refactoring that
function to create a seam is not something to do inside a security fix. This
reads the source and pins the property directly: any `except` guarding the
`require_review` read must set it True.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICE = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "web-server"
    / "server"
    / "services"
    / "agent_service.py"
)
_GATE_FUNCTION = "start_task_execution"
_FLAG = "require_review"


def _assigns_flag(node: ast.AST) -> bool:
    """Does this subtree assign ``require_review`` at all?"""
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == _FLAG for t in n.targets)
        for n in ast.walk(node)
    )


def _assigns_flag_true(handler: ast.ExceptHandler) -> bool:
    """Does this handler set ``require_review = True``?"""
    return any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == _FLAG for t in n.targets)
        and isinstance(n.value, ast.Constant)
        and n.value.value is True
        for n in ast.walk(handler)
    )


def _gate_handlers() -> list[ast.ExceptHandler]:
    """Every `except` whose `try` body decides ``require_review``."""
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == _GATE_FUNCTION
        ),
        None,
    )
    assert func is not None, (
        f"{_GATE_FUNCTION} not found -- it was renamed or moved. Update this "
        "test rather than deleting it; the property still matters."
    )
    return [
        handler
        for node in ast.walk(func)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if any(_assigns_flag(stmt) for stmt in node.body)
    ]


def test_the_review_gate_fails_closed_when_it_cannot_read_its_input() -> None:
    handlers = _gate_handlers()

    # Guard the measurement: if the try/except shape changes so that no handler
    # is found, this test would pass having checked nothing (Factory#832).
    assert handlers, (
        "no exception handler guards the require_review read -- either the gate "
        "moved or this test has stopped examining anything"
    )

    open_failing = [h.lineno for h in handlers if not _assigns_flag_true(h)]
    assert not open_failing, (
        f"handler(s) at line(s) {open_failing} swallow a metadata read failure "
        f"without setting {_FLAG} = True. require_review defaults to False and "
        "the consumer reads `if not require_review or force`, so a task that "
        "asked for human review would proceed without it (TFactory#1139)."
    )


def test_the_default_is_still_permissive_so_failing_closed_is_load_bearing() -> None:
    """If the default were True, the handlers above would be redundant.

    This pins WHY the fix is needed: the fail-closed assignment only matters
    because the default is False. Should someone later flip the default, this
    fails and the reader is told to re-examine the handlers rather than
    discovering silently that both are now no-ops.
    """
    source = _SERVICE.read_text(encoding="utf-8")
    assert f"{_FLAG} = False" in source, (
        f"{_FLAG} no longer defaults to False. The fail-closed handlers were "
        "written against that default -- re-check they are still meaningful."
    )

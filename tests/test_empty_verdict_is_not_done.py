"""#1253 follow-ups: two more places a measurement of zero read as a pass.

The triager's ``zero_tests`` rule closed the completion-envelope half. These
are the two remaining surfaces the same run touches.

1. ``verify_pipeline._VERDICT_STATUSES`` decided ``has_verdict`` from the
   STATUS NAME. It included ``evaluated_empty`` — which the evaluator writes
   from its "no completed subtasks" early exit, having evaluated nothing and
   written ``verdicts: []``. That recorded the Job ``done`` rather than
   ``stuck``: a zero rendered as a pass, in the very file whose #464 rule
   exists to catch exactly that.

2. ``POST /api/.../create-and-run`` shares a name with the ``task_create_and_run``
   MCP agent tool but is a different thing — it authors a spec and never runs
   verification. It said only ``success: true``.

Both directions are asserted. Dropping ``triaged_empty`` too, or refusing every
zero, would be the false-refusal half of the same defect.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import verify_pipeline as vp  # noqa: E402


def test_evaluated_empty_is_not_a_verdict() -> None:
    """It evaluated nothing, so the Job must not record `done`."""
    assert "evaluated_empty" not in vp._VERDICT_STATUSES, (
        "the evaluator writes evaluated_empty having produced verdicts: [] — "
        "counting it as a verdict records the Job done, which is a measurement "
        "of zero rendered as a pass"
    )


def test_triaged_empty_is_still_a_verdict() -> None:
    """The false-refusal direction: a 100% rejection IS a judgement.

    If this ever fails, healthy runs are being reaped as stalls — the same
    defect with the opposite sign.
    """
    assert "triaged_empty" in vp._VERDICT_STATUSES, (
        "the triager reaches triaged_empty after really judging candidates; "
        "reaping it as a stall would false-refuse a healthy run"
    )
    assert "triaged" in vp._VERDICT_STATUSES


def test_create_and_run_route_states_it_does_not_verify() -> None:
    """The route must say so, not leave the caller to infer it from success."""
    source = (
        Path(__file__).resolve().parent.parent
        / "apps"
        / "web-server"
        / "server"
        / "routes"
        / "execution.py"
    ).read_text()
    marker = source.index("async def create_and_run_task")
    body = source[marker : marker + 4000]
    assert '"verification": "not_started"' in body, (
        "create-and-run authors a spec and never verifies; the response must "
        "state that rather than returning a bare success: true"
    )
    assert "does NOT run verification" in body

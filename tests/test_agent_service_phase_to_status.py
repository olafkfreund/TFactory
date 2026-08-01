"""Tests for AgentService.phase_to_status (Factory#431).

`phase_to_status`'s mapping covers every current TaskPhase member, so its
`.get(phase, "in_progress")` fallback should never fire for well-formed
input today - but it is a trap for the next phase added without updating the
mapping: an unmapped phase would silently become "in_progress", asserting a
task is actively being worked on when we actually don't know that. The
frontend's TaskStatus union is a closed 5-value enum with no "unknown"
column, so changing the returned value would make the task vanish from the
kanban board; logging is the safe fix instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add apps/web-server/ to sys.path so ``from server.services...`` resolves.
# Same pattern as test_web_completion_relay.py.
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))

_BACKEND_PATH = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from server.services.agent_service import phase_to_status  # noqa: E402


def test_known_phases_are_unaffected():
    """Sanity: every real TaskPhase still maps as before."""
    from server.services.agent_service import TaskPhase

    assert phase_to_status(TaskPhase.CODING) == "in_progress"
    assert phase_to_status(TaskPhase.QA_REVIEW) == "ai_review"
    assert phase_to_status(TaskPhase.COMPLETED) == "human_review"


def test_unmapped_phase_logs_a_warning_naming_it(caplog):
    """Factory#431: pre-fix, an unmapped phase silently became "in_progress"
    with no observability. It must still return the safe closed-set default
    (the frontend has no "unknown" kanban column) but now log a warning that
    names the unmapped value, so a future un-mapped TaskPhase is caught
    instead of quietly asserting the task is in progress.
    """
    with caplog.at_level("WARNING"):
        result = phase_to_status("some_future_phase_nobody_mapped")  # type: ignore[arg-type]

    assert result == "in_progress"
    assert any(
        "some_future_phase_nobody_mapped" in record.message for record in caplog.records
    )

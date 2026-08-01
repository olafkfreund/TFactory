#!/usr/bin/env python3
"""
Tests for Timeline Data Models
===============================

Factory#431: a missing "status" field on a persisted TaskFileView record
must NOT silently become "active" - that would tell callers (get_active_tasks,
drift tracking, tracker_cli) that a task is still in flight when we actually
don't know its true lifecycle state. It must become an honest "unknown"
instead, and the deserializer must log a warning naming the task_id.
"""

from merge.timeline_models import TaskFileView


def _branch_point_dict() -> dict:
    return {
        "commit_hash": "abc123",
        "content": "print('hello')",
        "timestamp": "2026-01-01T00:00:00",
    }


class TestTaskFileViewFromDictStatus:
    """TaskFileView.from_dict must not fabricate an "active" status."""

    def test_missing_status_becomes_unknown_not_active(self):
        """Factory#431: pre-fix, `data.get("status", "active")` silently
        asserted a corrupt/legacy record (no "status" key) was still
        "active" - a plausible-looking but false claim of in-flight work.
        This must resolve to "unknown" so get_active_tasks() and drift
        tracking don't treat it as a real active task.
        """
        data = {
            "task_id": "task-missing-status",
            "branch_point": _branch_point_dict(),
            # "status" intentionally omitted - simulates a legacy/corrupt record.
        }

        view = TaskFileView.from_dict(data)

        assert view.status == "unknown"
        assert view.status != "active"

    def test_missing_status_logs_a_warning(self, caplog):
        """The unmapped input must be observable, not silent."""
        data = {
            "task_id": "task-missing-status",
            "branch_point": _branch_point_dict(),
        }

        with caplog.at_level("WARNING"):
            TaskFileView.from_dict(data)

        assert any(
            "task-missing-status" in record.message and "status" in record.message
            for record in caplog.records
        )

    def test_explicit_status_is_preserved(self):
        """A well-formed record's real status must still round-trip."""
        data = {
            "task_id": "task-1",
            "branch_point": _branch_point_dict(),
            "status": "merged",
        }

        view = TaskFileView.from_dict(data)

        assert view.status == "merged"

    def test_unknown_status_excluded_from_active_tasks(self):
        """An "unknown"-status view must not be counted as active."""
        view = TaskFileView.from_dict(
            {"task_id": "t", "branch_point": _branch_point_dict()}
        )
        assert view.status != "active"

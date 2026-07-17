"""The DELETE /sessions warnings must not leak server internals.

CodeQL py/stack-trace-exposure flagged `result["warnings"] = errors` where each
error carried `str(e)` and an absolute `sessions_dir`. An OSError stringifies to
its own path, so both handed an external caller our filesystem layout. The
detail belongs in the log; the caller gets a count.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.routes import terminal  # noqa: E402

_SECRET = "/srv/secret-root/tenant-42/.tfactory/terminal-sessions/terminal_a.json"


def test_unlink_failure_reports_a_count_not_the_exception_or_path(monkeypatch, tmp_path):
    sessions = tmp_path / ".tfactory" / "terminal-sessions"
    sessions.mkdir(parents=True)
    (sessions / "terminal_a.json").write_text("{}")

    monkeypatch.setattr(
        terminal, "load_projects", lambda: {"p": {"path": str(tmp_path)}}
    )

    real_unlink = Path.unlink

    def _boom(self, *a, **k):
        if self.name.startswith("terminal_"):
            # Mirrors a real OSError, whose str() embeds the absolute path.
            raise PermissionError(f"[Errno 13] Permission denied: '{_SECRET}'")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _boom)

    body = str(asyncio.run(terminal.clear_terminal_sessions(project=str(tmp_path))))

    assert "warnings" in body, f"the failure should still be reported: {body}"
    assert _SECRET not in body, body
    assert "Permission denied" not in body, body
    assert "Errno" not in body, body
    assert "could not be removed" in body, body

"""AIFactory#1766: the task GET returns the commit TFactory tested."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
_BACKEND = _WEB_SERVER.parent / "backend"
for _p in (str(_WEB_SERVER), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from server.routes import tfactory_tasks as tasks  # noqa: E402


@pytest.mark.parametrize(
    ("source", "want"),
    [({"source_sha": "abc123"}, "abc123"), ({"source_sha": ""}, None), (None, None)],
)
def test_get_task_returns_source_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, str] | None,
    want: str | None,
) -> None:
    spec = tmp_path / "workspaces" / "proj" / "specs" / "048-feat"
    (spec / "context").mkdir(parents=True)
    (spec / "status.json").write_text("{}")
    if source is not None:
        (spec / "context" / "source.json").write_text(json.dumps(source))
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    assert tasks.get_task("048-feat")["source_sha"] == want

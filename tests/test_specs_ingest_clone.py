"""RFC-0007 / PARR seam (#87 / handoff fix): ingest self-registers via git_url."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from server.routes import specs as specs_mod  # noqa: E402
from server.routes.specs import SpecIngestRequest, ingest_spec  # noqa: E402


@pytest.mark.asyncio
async def test_unknown_project_with_git_url_clones_and_registers(monkeypatch, tmp_path):
    seen = {}

    async def fake_clone(*, git_url, branch, credential):
        seen.update(git_url=git_url, branch=branch, cred=credential)
        (tmp_path / ".git").mkdir(exist_ok=True)
        return tmp_path

    def fake_create_ws(**kw):
        seen["project_root"] = kw.get("project_root")
        seen["source_branch"] = kw.get("source_branch")
        return {"spec_dir": str(tmp_path)}

    # project store empty -> unknown project
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    monkeypatch.setattr("server.routes.projects.save_projects", lambda p: None)
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fake_clone
    )
    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.create_spec_ingest_workspace",
        fake_create_ws,
    )

    req = SpecIngestRequest(
        project_id="olafkfreund-aifactory-test",
        spec_id="027-x",
        spec_text="## Acceptance Criteria\n- it works",
        git_url="https://github.com/olafkfreund/aifactory-test.git",
        source_branch="aifactory/027-x",
    )
    out = await ingest_spec(req)
    assert out["task_id"] == "027-x"
    assert seen["git_url"].endswith("aifactory-test.git")
    assert seen["branch"] == "aifactory/027-x"
    assert seen["project_root"] == str(tmp_path)  # cloned path used as root
    assert seen["source_branch"] == "aifactory/027-x"


@pytest.mark.asyncio
async def test_unknown_project_without_git_url_still_404(monkeypatch):
    monkeypatch.setattr("server.routes.projects.load_projects", lambda: {})
    req = SpecIngestRequest(
        project_id="nope", spec_id="1", spec_text="## Acceptance Criteria\n- x"
    )
    with pytest.raises(HTTPException) as ei:
        await ingest_spec(req)
    assert ei.value.status_code == 404

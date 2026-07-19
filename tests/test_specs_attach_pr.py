"""#964: POST /api/specs/{project}/{spec}/pr back-fills the PR onto an ingested
spec's source.json (handoff is sent before the PR exists) so the triager's
pr_comment step can post the verdict; posts immediately if already triaged."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from server.routes.specs import PrAttachRequest, attach_pr  # noqa: E402


def _make_spec(tmp_path: Path, monkeypatch, *, status: str | None = None) -> Path:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "server.routes.projects.load_projects", lambda: {"pid": {"name": "demo"}}
    )
    spec_dir = tmp_path / "workspaces" / "pid" / "specs" / "048-feat"
    (spec_dir / "context").mkdir(parents=True)
    (spec_dir / "findings").mkdir(parents=True)
    (spec_dir / "context" / "source.json").write_text(
        json.dumps({"mode": "spec_ingest", "spec_id": "048-feat"})
    )
    if status:
        (spec_dir / "status.json").write_text(json.dumps({"status": status}))
        (spec_dir / "findings" / "pr_comment_body.md").write_text("# Triage Report\n")
    return spec_dir


@pytest.mark.asyncio
async def test_attach_records_pr_on_source_json(tmp_path, monkeypatch) -> None:
    spec_dir = _make_spec(tmp_path, monkeypatch)
    out = await attach_pr(
        "demo", "048-feat", PrAttachRequest(pr_number=383, repo_slug="o/r")
    )
    assert out["attached"] is True
    src = json.loads((spec_dir / "context" / "source.json").read_text())
    assert src["pr_number"] == 383
    assert src["repo_slug"] == "o/r"
    # not yet triaged → nothing posted now
    assert out["posted"] is None


@pytest.mark.asyncio
async def test_attach_posts_when_already_triaged(tmp_path, monkeypatch) -> None:
    # dry-run stays on (flag unset) so no real gh shell-out
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)
    _make_spec(tmp_path, monkeypatch, status="triaged")
    out = await attach_pr(
        "demo", "048-feat", PrAttachRequest(pr_number=383, repo_slug="o/r")
    )
    assert out["posted"] is not None
    assert out["posted"]["dry_run"] is True
    assert out["posted"]["ok"] is True


@pytest.mark.asyncio
async def test_attach_unknown_spec_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "server.routes.projects.load_projects", lambda: {"pid": {"name": "demo"}}
    )
    with pytest.raises(HTTPException) as ei:
        await attach_pr("demo", "nope", PrAttachRequest(pr_number=1, repo_slug=None))
    assert ei.value.status_code == 404

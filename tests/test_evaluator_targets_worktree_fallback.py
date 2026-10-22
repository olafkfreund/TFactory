"""Target resolution falls back to the checkout's .tfactory.yml (#1347).

Only the task_control path writes ``context/tfactory_yml.json``; specs from
``POST /api/specs/ingest`` and the AIFactory handoff have just
``.worktree/.tfactory.yml``.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.evaluator_targets import _browser_target_url, _resolve_target

_YML = """\
version: 1
default_target: api
targets:
  - name: api
    type: http
    base_url: http://127.0.0.1:8000
"""


def _worktree_yml(spec_dir: Path, text: str = _YML) -> None:
    (spec_dir / ".worktree").mkdir(parents=True)
    (spec_dir / ".worktree" / ".tfactory.yml").write_text(text)


def test_worktree_yml_used_without_snapshot(tmp_path: Path) -> None:
    _worktree_yml(tmp_path)
    assert _browser_target_url(tmp_path, {}) == "http://127.0.0.1:8000"
    assert _resolve_target(tmp_path, {})["name"] == "api"


def test_snapshot_wins_over_worktree(tmp_path: Path) -> None:
    _worktree_yml(tmp_path)
    (tmp_path / "context").mkdir()
    (tmp_path / "context" / "tfactory_yml.json").write_text(
        json.dumps({"targets": [{"name": "s", "base_url": "http://snap/"}]})
    )
    assert _browser_target_url(tmp_path, {}) == "http://snap"


def test_invalid_worktree_yml_is_no_target(tmp_path: Path) -> None:
    _worktree_yml(tmp_path, "version: [unclosed\n")
    assert _browser_target_url(tmp_path, {}) is None
    assert _browser_target_url(tmp_path / "missing", {}) is None

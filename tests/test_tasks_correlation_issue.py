"""The /api/tasks list row must surface the PARR correlation key (GitHub issue
number) so the CFactory cockpit can attach a TFactory task to its issue-keyed
work item and render the test-stage lane (#94).

Before this, ``load_spec_metadata`` exposed title/status/phase/subtasks but no
issue field, so the cockpit fell back to the spec id, never matched the work
item, and the test lane showed empty even while verification ran. These tests
lock the resolver precedence (RFC-0002 contract -> source.json) and the wiring
into the task metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add web-server to path so server modules are importable (mirrors
# tests/test_task_skills.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.routes.tasks import (  # noqa: E402
    _resolve_correlation_issue,
    load_spec_metadata,
)


def _spec(tmp_path: Path, name: str = "001-feature") -> Path:
    d = tmp_path / name
    (d / "context").mkdir(parents=True)
    return d


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))


# --- _resolve_correlation_issue: precedence + coercion -----------------------


def test_resolves_issue_from_source_json(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write(spec / "context" / "source.json", {"issue_number": 42})
    assert _resolve_correlation_issue(spec) == 42


def test_resolves_issue_from_correlation_id_fallback(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write(spec / "context" / "source.json", {"correlation_id": "57"})
    assert _resolve_correlation_issue(spec) == 57


def test_contract_correlation_key_takes_precedence(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    # source.json says one thing, the RFC-0002 contract drop another — the
    # contract wins, matching the handback's own precedence.
    _write(spec / "context" / "source.json", {"issue_number": 42})
    _write(spec / "context" / "task_contract.json", {"correlation_key": "99"})
    assert _resolve_correlation_issue(spec) == 99


def test_embedded_contract_in_source_json(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write(
        spec / "context" / "source.json",
        {"contract": {"correlation_key": "73"}},
    )
    assert _resolve_correlation_issue(spec) == 73


def test_non_numeric_correlation_key_is_ignored(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write(spec / "context" / "source.json", {"correlation_key": "not-an-issue"})
    assert _resolve_correlation_issue(spec) is None


def test_no_provenance_returns_none(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    assert _resolve_correlation_issue(spec) is None


# --- load_spec_metadata: the key reaches the task-list row -------------------


def test_load_spec_metadata_surfaces_github_issue(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write(spec / "context" / "source.json", {"issue_number": 42})
    meta = load_spec_metadata(spec)
    assert meta["task_metadata"].get("githubIssueNumber") == 42


def test_existing_github_issue_not_overwritten(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    # requirements.json already carries the issue — keep it, don't clobber.
    _write(spec / "requirements.json", {"metadata": {"githubIssueNumber": 7}})
    _write(spec / "context" / "source.json", {"issue_number": 42})
    meta = load_spec_metadata(spec)
    assert meta["task_metadata"].get("githubIssueNumber") == 7

#!/usr/bin/env python3
"""Tests for the WS2 generic-ingestion seam — create_spec_ingest_workspace.

Verifies the no-AIFactory front door: a raw markdown / Gherkin / EARS spec
becomes a TFactory workspace (context/aifactory_spec.md + target-mode
source.json + status.json), with parse-before-create failure semantics.
Planner scheduling is disabled (schedule=False) so no SDK is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace  # noqa: E402

_GHERKIN = """Feature: Login
  Scenario: Successful login
    Given a registered user
    When they submit valid credentials
    Then a session is created
  Scenario: Bad password
    When they submit a wrong password
    Then login is rejected
"""

_MARKDOWN = """# Checkout

Some context.

## Acceptance Criteria
- Cart totals include tax
- Empty cart cannot check out
"""

_EARS = """The system shall reject expired coupons.
When the cart is empty, the system shall disable checkout.
"""


def _ingest(tmp_path: Path, text: str, **kw):
    return create_spec_ingest_workspace(
        project_id="proj",
        spec_id=kw.pop("spec_id", "spec1"),
        spec_text=text,
        root=tmp_path,
        schedule=False,
        **kw,
    )


def _spec_dir(tmp_path: Path, spec_id="spec1") -> Path:
    return tmp_path / "workspaces" / "proj" / "specs" / spec_id


# ─── happy paths per format ───────────────────────────────────────────────


def test_gherkin_creates_workspace(tmp_path):
    result = _ingest(tmp_path, _GHERKIN, target_paths=["src/auth.py"])
    assert result["source_format"] == "gherkin"
    assert result["ac_count"] >= 1
    assert result["planner_scheduled"] is False

    sd = _spec_dir(tmp_path)
    spec_md = (sd / "context" / "aifactory_spec.md").read_text()
    assert "## Acceptance Criteria" in spec_md
    assert "AC#1" in spec_md

    source = json.loads((sd / "context" / "source.json").read_text())
    assert source["mode"] == "spec_ingest"
    assert source["source_format"] == "gherkin"
    assert source["target_paths"] == ["src/auth.py"]

    status = json.loads((sd / "status.json").read_text())
    assert status["mode"] == "spec_ingest" and status["status"] == "pending"


def test_markdown_format(tmp_path):
    result = _ingest(tmp_path, _MARKDOWN)
    assert result["source_format"] == "markdown"
    assert result["ac_count"] == 2


def test_ears_format(tmp_path):
    result = _ingest(tmp_path, _EARS)
    assert result["source_format"] == "ears"
    assert result["ac_count"] >= 1


def test_format_override_respected(tmp_path):
    # Force markdown parsing on Gherkin-looking text.
    result = _ingest(tmp_path, _MARKDOWN, fmt="markdown")
    assert result["source_format"] == "markdown"


# ─── failure semantics ────────────────────────────────────────────────────


def test_no_criteria_raises_and_leaves_no_dir(tmp_path):
    with pytest.raises(ValueError):
        _ingest(tmp_path, "# Title only\n\njust prose, no criteria\n")
    # parse-before-create: nothing was left behind
    assert not _spec_dir(tmp_path).exists()


def test_existing_spec_dir_raises(tmp_path):
    _ingest(tmp_path, _MARKDOWN)
    with pytest.raises(FileExistsError):
        _ingest(tmp_path, _MARKDOWN)  # same spec_id → collision


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

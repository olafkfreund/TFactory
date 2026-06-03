"""Tests for the cloud assessment portal store (#133/#152)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.cloud import store


def _write_assessment(root: Path, aid: str, *, verdict="reject", failed=5, account="1") -> Path:
    d = root / aid
    (d / "diagrams").mkdir(parents=True)
    (d / "cloud_assessment.json").write_text(
        json.dumps({"provider": "aws", "account": account, "verdict": verdict, "failed": failed, "passed": 10})
    )
    (d / "cloud_assessment.md").write_text("# report")
    (d / "cloud_remediation_plan.md").write_text("# plan")
    (d / "cloud_issues.json").write_text('{"epic": {}, "children": []}')
    (d / "diagrams" / "cloud_topology.mmd").write_text("graph LR")
    return d


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "cloud-assessments"
    root.mkdir()
    monkeypatch.setenv("TFACTORY_CLOUD_ASSESSMENT_ROOT", str(root))
    return root


def test_list_empty(store_root) -> None:
    assert store.list_assessments() == []


def test_list_sorted_newest_first(store_root) -> None:
    import os
    import time

    a = _write_assessment(store_root, "aws-1-20260101", account="1")
    b = _write_assessment(store_root, "aws-2-20260102", account="2")
    # make b newer
    now = time.time()
    os.utime(a / "cloud_assessment.json", (now - 100, now - 100))
    os.utime(b / "cloud_assessment.json", (now, now))
    ids = [x["id"] for x in store.list_assessments()]
    assert ids[0] == "aws-2-20260102"
    assert store.list_assessments()[0]["account"] == "2"


def test_read_assessment(store_root) -> None:
    _write_assessment(store_root, "aws-1-x")
    d = store.read_assessment("aws-1-x")
    assert d["present"] is True
    assert d["json"]["verdict"] == "reject"
    assert d["reportMarkdown"] == "# report"
    assert d["remediationMarkdown"] == "# plan"
    assert d["diagramMermaid"] == "graph LR"
    assert d["issuesJson"]


def test_read_missing_returns_none(store_root) -> None:
    assert store.read_assessment("nope") is None


def test_safe_id_rejects_traversal(store_root) -> None:
    assert store.read_assessment("../etc") is None
    assert store.download_path("../../x", "report.md") is None


def test_download_path_md_and_json(store_root) -> None:
    _write_assessment(store_root, "aws-1-x")
    assert store.download_path("aws-1-x", "report.md").name == "cloud_assessment.md"
    assert store.download_path("aws-1-x", "remediation.md").name == "cloud_remediation_plan.md"
    assert store.download_path("aws-1-x", "issues.json").name == "cloud_issues.json"
    assert store.download_path("aws-1-x", "bogus.kind") is None

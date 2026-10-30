"""Tests for RFC-0001 correlation sync: completion event + handback (#249)."""

from __future__ import annotations

import json

import pytest
from agents.handback import build_correction_request
from agents.handback.send import _correlation_key_for, send_correction
from agents.triager import _correlation_key

_SOURCE = {
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    }
}
_VERDICTS = {
    "verdicts": [
        {"test_id": "t_bad", "verdict": "reject", "reasons": ["x"], "lane": "api"}
    ]
}
_TRIAGE = {"rejected": [{"test_id": "t_bad", "test_file": "tests/test_x.py"}]}


@pytest.fixture
def spec(tmp_path):
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-login"
    (d / "context").mkdir(parents=True)
    (d / "findings").mkdir()
    return d


def _write_contract(spec, key):
    (spec / "context" / "aifactory_plan.json").write_text(
        json.dumps(
            {
                "contract_version": "2",
                "correlation_key": key,
                "tfactory": {"lanes": ["unit"]},
            }
        )
    )


# ─── triager _correlation_key precedence ─────────────────────────────────


def test_correlation_prefers_contract_key(spec):
    _write_contract(spec, "PF-123")
    # even with a competing issue number in status, the contract wins
    assert _correlation_key(spec, {"issue_number": 9}, {}) == "PF-123"


def test_correlation_without_repo_falls_back_to_synthetic(spec):
    assert _correlation_key(spec, {"issue_number": 42}, {}) == f"tf-demo:{spec.name}"


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("github", "o/r#7"), ("gitlab", "gitlab:o/r#7"), (None, "o/r#7")],
)
def test_correlation_is_scoped_to_the_repo(spec, provider, expected):
    """#1775: <repo>#<n>, repo reference per RFC-0020 3.5."""
    source = {"repo_slug": "o/r", "aifactory": {"github_issue": 7}}
    if provider:
        source["provider"] = provider
    assert _correlation_key(spec, {}, source) == expected


def test_correlation_reads_the_pfactory_pickup_repo(spec):
    """The PFactory pickup writes `repo`, not `repo_slug` (integrations/pfactory/run.py)."""
    source = {"pfactory": True, "issue_number": 7, "repo": "o/r"}
    assert _correlation_key(spec, {}, source) == "o/r#7"


def test_correlation_without_repo_is_synthetic(spec):
    key = _correlation_key(spec, {}, {"aifactory": {"github_issue": 7}})
    assert key == f"tf-demo:{spec.name}"


def test_handback_key_is_scoped_to_the_repo(spec):
    (spec / "context" / "source.json").write_text(
        json.dumps({"issue_number": 55, "repo_slug": "o/r", "provider": "gitlab"})
    )
    assert _correlation_key_for(spec) == "gitlab:o/r#55"


def test_correlation_synthetic_fallback(spec):
    key = _correlation_key(spec, {"spec_id": "001-login"}, {})
    assert key == "tf-demo:001-login"


# ─── handback correlation_key_for ────────────────────────────────────────


def test_handback_key_from_contract(spec):
    _write_contract(spec, "PF-777")
    assert _correlation_key_for(spec) == "PF-777"


def test_handback_key_from_source_issue(spec):
    (spec / "context" / "source.json").write_text(json.dumps({"issue_number": 55}))
    assert _correlation_key_for(spec) == f"tf-demo:{spec.name}"


def test_handback_key_none_when_absent(spec):
    assert _correlation_key_for(spec) is None


# ─── handback payload carries correlation_key end-to-end ─────────────────


def test_handback_send_includes_correlation_key(spec):
    _write_contract(spec, "PF-9")
    captured = {}
    res = send_correction(
        build_correction_request(_VERDICTS, _TRIAGE, _SOURCE),
        spec,
        dry_run=False,
        confirm=True,
        sender_fn=lambda payload: captured.update(payload) or {"ok": True},
    )
    assert res.sent is True
    assert captured["correlation_key"] == "PF-9"
    # artifact also records it
    doc = json.loads((spec / "findings" / "handback_request.json").read_text())
    assert doc["correlation_key"] == "PF-9"


def test_handback_artifact_records_key_in_dry_run(spec):
    _write_contract(spec, "PF-5")
    send_correction(
        build_correction_request(_VERDICTS, _TRIAGE, _SOURCE), spec, dry_run=True
    )
    doc = json.loads((spec / "findings" / "handback_request.json").read_text())
    assert doc["correlation_key"] == "PF-5"


def test_build_only_handoff_keeps_its_key(tmp_path):
    """#1775: AIFactory's build-only contract has no RFC-0002 markers, so no
    task_contract.json is written; the declared key must still win."""
    from agents.handback.send import _correlation_key_for as handback_key
    from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace

    create_spec_ingest_workspace(
        project_id="proj",
        spec_id="spec-1",
        spec_text="# Spec\n\n- AC#1: the widget ships\n",
        root=tmp_path,
        schedule=False,
        contract={"provenance": {"github_issue": 382}, "correlation_key": "o/r#382"},
    )
    spec_dir = tmp_path / "workspaces" / "proj" / "specs" / "spec-1"
    assert not (spec_dir / "context" / "task_contract.json").exists()
    source = json.loads((spec_dir / "context" / "source.json").read_text())
    status = json.loads((spec_dir / "status.json").read_text())
    assert _correlation_key(spec_dir, status, source) == "o/r#382"
    assert handback_key(spec_dir) == "o/r#382"


def _spec_in(tmp_path, project, spec_id):
    d = tmp_path / "workspaces" / project / "specs" / spec_id
    (d / "context").mkdir(parents=True)
    return d


def _src(d, **extra):
    (d / "context" / "source.json").write_text(json.dumps({"issue_number": 5, **extra}))


def test_synthetic_key_differs_across_projects(tmp_path):
    # #1972: spec ids repeat across projects; the synthetic key must not.
    a, b = _spec_in(tmp_path, "p1", "003-x"), _spec_in(tmp_path, "p2", "003-x")
    assert _correlation_key(a, {}, {}) == "tf-p1:003-x"
    assert _correlation_key(b, {}, {}) == "tf-p2:003-x"
    _src(a)
    _src(b)
    assert _correlation_key_for(a) == "tf-p1:003-x"
    assert _correlation_key_for(b) == "tf-p2:003-x"


def test_project_id_from_status_or_source_beats_the_layout(tmp_path):
    # Registered project (<path>/.tfactory/specs/<sid>) and a worktree copy.
    reg = tmp_path / "proj" / ".tfactory" / "specs" / "003-x"
    wt = tmp_path / "wt" / "deep" / "003-x"
    for d in (reg, wt):
        (d / "context").mkdir(parents=True)
    assert _correlation_key(reg, {"project_id": "p9"}, {}) == "tf-p9:003-x"
    assert _correlation_key(wt, {}, {"project_id": "p8"}) == "tf-p8:003-x"
    (reg / "status.json").write_text(json.dumps({"project_id": "p9"}))
    _src(reg)
    assert _correlation_key_for(reg) == "tf-p9:003-x"
    _src(wt, project_id="p8")
    assert _correlation_key_for(wt) == "tf-p8:003-x"


def test_no_project_id_outside_the_workspaces_layout_gives_no_key(tmp_path, caplog):
    reg = tmp_path / "proj" / ".tfactory" / "specs" / "003-x"
    (reg / "context").mkdir(parents=True)
    _src(reg)
    with caplog.at_level("WARNING"):
        assert _correlation_key(reg, {}, {}) is None
        assert _correlation_key_for(reg) is None
    assert ".tfactory" not in caplog.text


def test_overlong_synthetic_key_is_none(tmp_path, caplog):
    d = _spec_in(tmp_path, "p1", "s" * 130)
    _src(d)
    with caplog.at_level("WARNING"):
        assert _correlation_key(d, {}, {}) is None
        assert _correlation_key_for(d) is None
    assert "max 128" in caplog.text
    assert "s" * 20 not in caplog.text  # length only, no ids

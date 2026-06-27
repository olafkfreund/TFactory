"""portal-ui dispatch manifest + visual-inspection adapter (#553 wiring)."""

import json
from pathlib import Path

import pytest

from portal_testing.dispatch import build_portal_ui_job_manifest, portal_ui_job_name
from portal_testing.visual_inspection_adapter import build_run_dir


def test_job_manifest_shape():
    m = build_portal_ui_job_manifest(
        "tfactory", "r123", image="img:nix", namespace="factory"
    )
    assert m["kind"] == "Job"
    assert m["metadata"]["name"] == portal_ui_job_name("tfactory", "r123")
    assert m["metadata"]["labels"]["portal"] == "tfactory"
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == "img:nix"
    assert c["command"] == ["python", "-m", "portal_testing.run"]
    assert c["args"][:1] == ["tfactory"] and "--visual-inspection" in c["args"]
    # MFA creds come from a Secret via env (never argv).
    env = {e["name"]: e for e in c["env"]}
    for var in ("TEST_USER", "TEST_PASSWORD", "TEST_TOTP_SECRET"):
        assert env[var]["valueFrom"]["secretKeyRef"]["key"] == var
    flat = " ".join(c["args"])
    assert "TEST_TOTP_SECRET" not in flat and "password" not in flat.lower()


def test_unknown_portal_rejected():
    with pytest.raises(ValueError):
        build_portal_ui_job_manifest("nope", "r1")


def test_adapter_builds_visual_inspection_run(tmp_path):
    report_dir = tmp_path / "tfactory"
    (report_dir / "screenshots").mkdir(parents=True)
    (report_dir / "screenshots" / "01-landing.png").write_bytes(b"\x89PNG")
    (report_dir / "report.md").write_text(
        "# T\n- **Auth:** ... logged in: **True**\n\n## Coverage\n\n"
        "| Nav items | Dropdowns | Dialogs | Screenshots | Findings |\n|---|---|---|---|---|\n"
        "| 11 | 4 | 2 | 14 | 0 |\n\n## Findings\n\n- None — all good.\n\n## Walkthrough\n"
    )
    run_dir = build_run_dir(
        "tfactory", report_dir, "run-xyz", dest_parent=tmp_path / "_vi"
    )
    meta = json.loads((run_dir / "meta.json").read_text())
    # Canonical Visual Inspection schema (what the tab renders).
    assert meta["verdict"] == "pass"
    assert meta["counts"] == {"steps": 14, "passed": 14, "failed": 0}
    assert meta["coverage"]["findings"] == 0
    assert meta["portal"] == "tfactory"
    assert (run_dir / "screenshots" / "01-landing.png").is_file()
    assert json.loads((run_dir / "issues.json").read_text()) == []


def test_adapter_verdict_attention_on_findings(tmp_path):
    report_dir = tmp_path / "p"
    report_dir.mkdir()
    (report_dir / "report.md").write_text(
        "# P\nlogged in: **True**\n\n## Coverage\n\n"
        "| N | D | Dl | S | F |\n|-|-|-|-|-|\n| 7 | 1 | 2 | 10 | 3 |\n\n"
        "## Findings\n\n- **Console error** on X\n\n## Walkthrough\n"
    )
    meta = json.loads(
        (
            build_run_dir("pfactory", report_dir, "r", dest_parent=tmp_path / "v")
            / "meta.json"
        ).read_text()
    )
    assert meta["verdict"] == "attention"
    assert meta["counts"] == {"steps": 10, "passed": 7, "failed": 3}


def test_job_mounts_data_pvc_and_pull_secret():
    m = build_portal_ui_job_manifest("cfactory", "r9", data_pvc="tfactory-data")
    spec = m["spec"]["template"]["spec"]
    assert spec["imagePullSecrets"] == [{"name": "ghcr-pull"}]
    vol = spec["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "tfactory-data"
    c = spec["containers"][0]
    assert {"name": "data", "mountPath": "/home/nonroot/.tfactory"} in c["volumeMounts"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["HOME"] == "/home/nonroot"


def test_publish_as_tfactory_spec(tmp_path, monkeypatch):
    from portal_testing.visual_inspection_adapter import publish_as_tfactory_spec

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    report_dir = tmp_path / "tfactory"
    (report_dir / "screenshots").mkdir(parents=True)
    (report_dir / "screenshots" / "01-landing.png").write_bytes(b"\x89PNG")
    (report_dir / "report.md").write_text(
        "# T\nlogged in: **True**\n\n## Coverage\n\n"
        "| N | D | Dl | S | F |\n|-|-|-|-|-|\n| 11 | 4 | 2 | 14 | 0 |\n\n## Findings\n\n- None\n\n## Walkthrough\n"
    )
    spec_dir = publish_as_tfactory_spec("tfactory", report_dir, "vrun-1")
    # Must land where /api/tfactory/tasks globs: workspaces/<pid>/specs/<sid>/
    assert spec_dir.match("*/workspaces/portal-ui/specs/vrun-1")
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged"  # → Report lane
    assert status["project_id"] == "portal-ui"
    assert status["verdict"] == "pass"
    assert (spec_dir / "screenshots" / "01-landing.png").is_file()
    assert (spec_dir / "findings" / "verdicts.json").is_file()

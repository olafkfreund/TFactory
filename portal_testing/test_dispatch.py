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
    assert meta["counts"] == {"steps": 17, "passed": 17, "failed": 0}
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
    # A console error → attention, but it is NOT a failed interaction.
    assert meta["verdict"] == "attention"
    assert meta["counts"] == {"steps": 10, "passed": 10, "failed": 0}


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
    assert (spec_dir / "findings" / "triage_report.md").is_file()
    assert (spec_dir / "findings" / "screenshots" / "01-landing.png").is_file()
    assert not (spec_dir / "findings" / "verdicts.json").exists()


# ─── #895: the Screencast link must name where the recording landed ─────


def _run_with_recording(tmp_path):
    report_dir = tmp_path / "tfactory"
    (report_dir / "video").mkdir(parents=True)
    (report_dir / "video" / "tfactory.webm").write_bytes(b"\x1a\x45\xdf\xa3")
    (report_dir / "report.md").write_text(
        "# T\nlogged in: **True**\n"
        "- **Screencast:** [`video/tfactory.webm`](video/tfactory.webm)\n\n"
        "## Coverage\n\n| N | D | Dl | S | F |\n|-|-|-|-|-|\n| 1 | 0 | 0 | 1 | 0 |\n\n"
        "## Findings\n\n- None\n\n## Walkthrough\n"
    )
    return report_dir


def test_published_report_links_the_recording_where_it_landed(tmp_path, monkeypatch):
    """The link and the file must agree in the published tree.

    report.py writes it relative to the run dir (`video/x.webm`); publishing
    moves the recording to findings/videos/. Nothing rewrote the link, so the
    published report pointed at a path that does not exist there (#895).
    """
    from portal_testing.visual_inspection_adapter import publish_as_tfactory_spec

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    spec_dir = publish_as_tfactory_spec(
        "tfactory", _run_with_recording(tmp_path), "vrun-vid"
    )
    published = (spec_dir / "report.md").read_text()
    assert "findings/videos/tfactory.webm" in published
    assert "(video/tfactory.webm)" not in published
    # The link resolves inside the published tree — that is the whole point.
    assert (spec_dir / "findings" / "videos" / "tfactory.webm").is_file()


def test_triage_report_drops_the_screencast_link(tmp_path, monkeypatch):
    """The rendered Report tab resolves no relative path, so a link there 404s —
    same reason the screenshot images are stripped. The Evidence tab plays the
    recording from findings/videos/."""
    from portal_testing.visual_inspection_adapter import publish_as_tfactory_spec

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    spec_dir = publish_as_tfactory_spec(
        "tfactory", _run_with_recording(tmp_path), "vrun-vid2"
    )
    assert "Screencast:" not in (spec_dir / "findings" / "triage_report.md").read_text()


def test_visual_inspection_copy_drops_the_screencast_link(tmp_path):
    """The Visual Reports copy carries no recording at all, so its Screencast
    link was doubly broken."""
    from portal_testing.visual_inspection_adapter import build_run_dir

    run_dir = build_run_dir(
        "tfactory", _run_with_recording(tmp_path), "r", dest_parent=tmp_path / "vi"
    )
    report = (run_dir / "report.md").read_text()
    assert "Screencast:" not in report
    assert "tfactory.webm" not in report


def test_a_lost_recording_drops_the_link_rather_than_inventing_one(
    tmp_path, monkeypatch
):
    """The report claims a screencast but the recording never made it.

    A link is only worth publishing when it names a file that is there. If the
    recording was lost, the honest published report has no Screencast line —
    not a plausible-looking path to a file that does not exist.
    """
    from portal_testing.visual_inspection_adapter import publish_as_tfactory_spec

    monkeypatch.setenv("TFACTORY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    report_dir = tmp_path / "p"
    report_dir.mkdir()
    (report_dir / "report.md").write_text(
        "# P\nlogged in: **True**\n"
        "- **Screencast:** [`video/pfactory.webm`](video/pfactory.webm)\n\n"
        "## Coverage\n\n| N | D | Dl | S | F |\n|-|-|-|-|-|\n| 1 | 0 | 0 | 1 | 0 |\n\n"
        "## Findings\n\n- None\n\n## Walkthrough\n"
    )
    spec_dir = publish_as_tfactory_spec("pfactory", report_dir, "vrun-novid")
    published = (spec_dir / "report.md").read_text()
    assert "Screencast" not in published
    assert ".webm" not in published
    assert not (spec_dir / "findings" / "videos").exists()


def test_adapter_counts_interaction_failures(tmp_path):
    import json

    from portal_testing.visual_inspection_adapter import build_run_dir

    rd = tmp_path / "p"
    rd.mkdir()
    (rd / "report.md").write_text(
        "# P\nlogged in: **True**\n\n## Coverage\n\n"
        "| N | D | Dl | S | F |\n|-|-|-|-|-|\n| 5 | 0 | 0 | 3 | 2 |\n\n"
        "## Findings\n\n- **Interaction failed** on Tests (nav): click failed\n"
        "- **Console error** on Files\n\n## Walkthrough\n"
    )
    meta = json.loads(
        (
            build_run_dir("pfactory", rd, "r", dest_parent=tmp_path / "v") / "meta.json"
        ).read_text()
    )
    # 1 of 5 failed → not a majority → attention (the test still ran).
    assert meta["verdict"] == "attention"
    assert meta["counts"] == {"steps": 5, "passed": 4, "failed": 1}


def test_startup_delay_staggers_login_without_leaking_creds():
    m = build_portal_ui_job_manifest("aifactory", "r1", startup_delay_seconds=80)
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["command"][0:2] == ["sh", "-c"]
    assert c["command"][2].startswith("sleep 80; exec ")
    assert "portal_testing.run" in c["command"][2] and "aifactory" in c["command"][2]
    assert c["args"] == []
    # MFA creds still come from the Secret via env — never on the command line.
    flat = " ".join(c["command"])
    assert "TEST_PASSWORD" not in flat and "TEST_TOTP_SECRET" not in flat
    env = {e["name"]: e for e in c["env"]}
    assert (
        env["TEST_TOTP_SECRET"]["valueFrom"]["secretKeyRef"]["key"]
        == "TEST_TOTP_SECRET"
    )


def test_no_delay_keeps_plain_command():
    m = build_portal_ui_job_manifest("tfactory", "r2")
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["command"] == ["python", "-m", "portal_testing.run"]
    assert c["args"][0] == "tfactory"


# ─── #875: the Job must co-mount the claim the control plane reads ──────


class _FakeMount:
    def __init__(self, name, mount_path):
        self.name = name
        self.mount_path = mount_path


class _FakeContainer:
    def __init__(self, volume_mounts):
        self.volume_mounts = volume_mounts


class _FakeClaim:
    def __init__(self, claim_name):
        self.claim_name = claim_name


class _FakeVolume:
    def __init__(self, name, claim_name=None):
        self.name = name
        self.persistent_volume_claim = _FakeClaim(claim_name) if claim_name else None


class _FakeSpec:
    def __init__(self, containers, volumes):
        self.containers = containers
        self.volumes = volumes


class _FakePod:
    def __init__(self, spec):
        self.spec = spec


def _pod(mount_path="/home/nonroot/.tfactory", claim="tfactory-data-rwx"):
    return _FakePod(
        _FakeSpec(
            containers=[_FakeContainer([_FakeMount("data", mount_path)])],
            volumes=[_FakeVolume("tmp"), _FakeVolume("data", claim)],
        )
    )


def test_claim_for_mount_path_follows_the_control_plane():
    """The claim is read off the pod, not guessed.

    #875: the default named an orphan PVC (`tfactory-data`, RWO/local-path)
    while the deployment mounted `tfactory-data-rwx`. The harness ran, publish
    reported success, and the run was invisible — written to a volume nothing
    reads, with no error to notice.
    """
    from portal_testing.dispatch import _claim_for_mount_path

    assert (
        _claim_for_mount_path(_pod(), "/home/nonroot/.tfactory") == "tfactory-data-rwx"
    )


def test_claim_for_mount_path_ignores_a_different_mount():
    """A volume mounted somewhere else is not the data claim."""
    from portal_testing.dispatch import _claim_for_mount_path

    assert (
        _claim_for_mount_path(_pod(mount_path="/tmp"), "/home/nonroot/.tfactory")
        is None
    )


def test_claim_for_mount_path_ignores_a_non_pvc_volume():
    """An emptyDir at the data path yields no claim (fall back, don't invent one)."""
    from portal_testing.dispatch import _claim_for_mount_path

    assert _claim_for_mount_path(_pod(claim=None), "/home/nonroot/.tfactory") is None


def test_resolve_data_pvc_prefers_the_env_override(monkeypatch):
    from portal_testing import dispatch

    monkeypatch.setenv("TFACTORY_DATA_PVC", "some-other-claim")
    assert dispatch.resolve_data_pvc() == "some-other-claim"


def test_resolve_data_pvc_falls_back_off_cluster(monkeypatch):
    """Outside a pod there is nothing to read; the constant must still be the
    claim the deployment mounts, not the orphan."""
    from portal_testing import dispatch

    monkeypatch.delenv("TFACTORY_DATA_PVC", raising=False)
    assert dispatch.resolve_data_pvc() == "tfactory-data-rwx"
    assert dispatch.DEFAULT_DATA_PVC == "tfactory-data-rwx"


def test_default_job_mounts_the_resolved_claim(monkeypatch):
    from portal_testing import dispatch

    monkeypatch.delenv("TFACTORY_DATA_PVC", raising=False)
    m = dispatch.build_portal_ui_job_manifest("tfactory", "r1")
    vol = m["spec"]["template"]["spec"]["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "tfactory-data-rwx"


def test_job_pods_do_not_join_the_tfactory_service():
    """Regression for the portal-ui lane taking its own target offline.

    The `tfactory` Service selects `app=tfactory`. A Job pod carrying that label
    becomes a Service endpoint; it listens on nothing, so kube-proxy hands it a
    share of real requests and Cloudflare returns 502 for the duration of the
    test run. Observed: zero origin refusals for three hours, then refusals in
    exactly the two minutes a portal-ui Job was running.
    """
    from portal_testing.dispatch import build_portal_ui_job_manifest

    m = build_portal_ui_job_manifest("tfactory", "r1")
    pod_labels = m["spec"]["template"]["metadata"]["labels"]
    assert pod_labels.get("app") != "tfactory", (
        "portal-ui Job pods must not match the tfactory Service selector"
    )
    assert pod_labels["lane"] == "portal-ui"

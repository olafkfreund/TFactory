"""#1625: no TFactory Job pod inherits Kubernetes service-link env vars."""

from __future__ import annotations

from pathlib import Path

from tools.runners.kube_sandbox import build_job_manifest as sandbox_manifest

_CHARTS = Path(__file__).resolve().parent.parent / "charts" / "tfactory" / "templates"


def _pod(m):
    return m["spec"]["template"]["spec"]


def test_kube_sandbox_pod_disables_service_links():
    assert _pod(sandbox_manifest("j", "img", ["true"]))["enableServiceLinks"] is False


def test_portal_pod_disables_service_links():
    from portal_testing.dispatch import build_portal_ui_job_manifest

    m = build_portal_ui_job_manifest(
        "tfactory", "r", image="img", namespace="ns", data_pvc="d"
    )
    assert _pod(m)["enableServiceLinks"] is False


def test_chart_job_templates_disable_service_links():
    for name in ("regression-cronjob.yaml", "job-migrations.yaml"):
        assert "enableServiceLinks: false" in (_CHARTS / name).read_text(), name

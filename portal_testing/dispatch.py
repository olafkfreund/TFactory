"""Dispatch a portal-ui test as a Kubernetes Job (the portal-ui task type, #553).

The harness runs on the **nix browser image** (python+playwright+browsers, the
nix_provisioner stack) — the control-plane pod has no browser, so the test runs
as a Job, mirroring TFactory's verify-lane dispatch. The MFA test-user
credentials come from a Secret via env (never argv). Results are written into the
visual-inspection store (see ``visual_inspection_adapter``) so they surface in
the portal's Visual Reports tab.

``build_portal_ui_job_manifest`` is pure (returns a manifest dict) and unit
tested; ``dispatch_portal_ui`` submits it via the in-cluster client when
available.
"""

from __future__ import annotations

import os
from typing import Any

# The portals the capability knows about (kept in sync with config.PORTALS).
PORTAL_KEYS = ("pfactory", "aifactory", "tfactory", "cfactory")

DEFAULT_IMAGE = "tfactory-runner-portal-ui:nix"
DEFAULT_NAMESPACE = "factory"
# Secret holding the enrolled MFA test user (provisioned by keycloak_provision).
DEFAULT_MFA_SECRET = "portal-ui-test-user"
_MFA_ENV = ("TEST_USER", "TEST_PASSWORD", "TEST_TOTP_SECRET")


def portal_ui_job_name(portal_key: str, run_id: str) -> str:
    return f"portal-ui-{portal_key}-{run_id}".lower()[:63].rstrip("-")


def build_portal_ui_job_manifest(
    portal_key: str,
    run_id: str,
    *,
    image: str | None = None,
    namespace: str | None = None,
    mfa_secret: str | None = None,
    browsers_path: str | None = None,
) -> dict[str, Any]:
    """Build the k8s Job manifest that runs the portal-ui harness for one portal.

    The Job runs ``python -m portal_testing.run <portal> --visual-inspection``
    on the nix browser image, with the MFA credentials sourced from a Secret and
    the nix-provided browsers wired via ``PLAYWRIGHT_BROWSERS_PATH``.
    """
    if portal_key not in PORTAL_KEYS:
        raise ValueError(f"unknown portal {portal_key!r}; have {PORTAL_KEYS}")
    image = image or os.environ.get("PORTAL_UI_IMAGE", DEFAULT_IMAGE)
    namespace = namespace or os.environ.get("TFACTORY_NAMESPACE", DEFAULT_NAMESPACE)
    mfa_secret = mfa_secret or os.environ.get(
        "PORTAL_UI_MFA_SECRET", DEFAULT_MFA_SECRET
    )
    name = portal_ui_job_name(portal_key, run_id)

    env: list[dict[str, Any]] = [
        {"name": var, "valueFrom": {"secretKeyRef": {"name": mfa_secret, "key": var}}}
        for var in _MFA_ENV
    ]
    env.append({"name": "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS", "value": "true"})
    if browsers_path or os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        env.append(
            {
                "name": "PLAYWRIGHT_BROWSERS_PATH",
                "value": browsers_path or os.environ["PLAYWRIGHT_BROWSERS_PATH"],
            }
        )

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "tfactory", "lane": "portal-ui", "portal": portal_key},
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": {"app": "tfactory", "lane": "portal-ui"}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "portal-ui",
                            "image": image,
                            "command": ["python", "-m", "portal_testing.run"],
                            "args": [
                                portal_key,
                                "--visual-inspection",
                                "--run-id",
                                run_id,
                            ],
                            "env": env,
                        }
                    ],
                },
            },
        },
    }


def dispatch_portal_ui(portal_key: str, run_id: str, **kwargs: Any) -> str:
    """Submit the portal-ui Job to the cluster. Returns the Job name.

    Best-effort: requires the kubernetes client + in-cluster config. Raises a
    clear error if unavailable (e.g. running outside a pod) so callers can fall
    back to a local ``python -m portal_testing.run`` invocation.
    """
    manifest = build_portal_ui_job_manifest(portal_key, run_id, **kwargs)
    try:
        from kubernetes import client, config  # type: ignore
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "kubernetes client not available; run the harness locally"
        ) from e
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001 - fall back to kubeconfig
        config.load_kube_config()
    batch = client.BatchV1Api()
    batch.create_namespaced_job(
        namespace=manifest["metadata"]["namespace"], body=manifest
    )
    return manifest["metadata"]["name"]

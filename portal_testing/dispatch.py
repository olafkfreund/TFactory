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
import shlex
from typing import Any

# The portals the capability knows about (kept in sync with config.PORTALS).
PORTAL_KEYS = ("pfactory", "aifactory", "tfactory", "cfactory")

DEFAULT_IMAGE = "ghcr.io/olafkfreund/tfactory-runner-portal-ui:latest"
DEFAULT_NAMESPACE = "factory"
# Secret holding the enrolled MFA test user (provisioned by keycloak_provision).
DEFAULT_MFA_SECRET = "portal-ui-test-user"
_MFA_ENV = ("TEST_USER", "TEST_PASSWORD", "TEST_TOTP_SECRET")
# The control-plane data PVC carries the Visual Inspection store at
# ~/.tfactory/visual-inspections. Co-mounting it (single-node) lets the Job's
# publish surface in the portal's Visual Reports tab.
DEFAULT_DATA_PVC = "tfactory-data"
_HOME = "/home/nonroot"


def portal_ui_job_name(portal_key: str, run_id: str) -> str:
    return f"portal-ui-{portal_key}-{run_id}".lower()[:63].rstrip("-")


def build_portal_ui_job_manifest(
    portal_key: str,
    run_id: str,
    *,
    image: str | None = None,
    namespace: str | None = None,
    mfa_secret: str | None = None,
    data_pvc: str | None = None,
    startup_delay_seconds: int = 0,
) -> dict[str, Any]:
    """Build the k8s Job manifest that runs the portal-ui harness for one portal.

    The Job runs ``python -m portal_testing.run <portal> --visual-inspection``
    on the portal-ui runner image (MS Playwright base — chromium + browsers
    baked), with the MFA credentials sourced from a Secret (via env, never argv).
    It co-mounts the control-plane data PVC at ``~/.tfactory`` so the published
    run lands in the Visual Inspection store the portal's tab reads.

    ``startup_delay_seconds`` prepends a ``sleep`` before the harness so Jobs
    submitted together stagger their Keycloak logins. This is REQUIRED when
    running multiple portals with the same MFA user: a TOTP code is one-time-use,
    so two logins in the same 30s window collide (the second is rejected). Pick a
    delay > 30s per portal index. ``exec`` keeps the MFA creds in env (not argv).
    """
    if portal_key not in PORTAL_KEYS:
        raise ValueError(f"unknown portal {portal_key!r}; have {PORTAL_KEYS}")
    image = image or os.environ.get("PORTAL_UI_IMAGE", DEFAULT_IMAGE)
    namespace = namespace or os.environ.get("TFACTORY_NAMESPACE", DEFAULT_NAMESPACE)
    mfa_secret = mfa_secret or os.environ.get(
        "PORTAL_UI_MFA_SECRET", DEFAULT_MFA_SECRET
    )
    data_pvc = data_pvc or os.environ.get("TFACTORY_DATA_PVC", DEFAULT_DATA_PVC)
    name = portal_ui_job_name(portal_key, run_id)

    env: list[dict[str, Any]] = [
        {"name": var, "valueFrom": {"secretKeyRef": {"name": mfa_secret, "key": var}}}
        for var in _MFA_ENV
    ]
    env.append({"name": "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS", "value": "true"})
    # HOME resolves the Visual Inspection store onto the co-mounted data PVC.
    env.append({"name": "HOME", "value": _HOME})

    run_args = [portal_key, "--visual-inspection", "--run-id", run_id]
    if startup_delay_seconds > 0:
        inner = shlex.join(["python", "-m", "portal_testing.run", *run_args])
        command = ["sh", "-c", f"sleep {int(startup_delay_seconds)}; exec {inner}"]
        args: list[str] = []
    else:
        command = ["python", "-m", "portal_testing.run"]
        args = run_args

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
                    "imagePullSecrets": [{"name": "ghcr-pull"}],
                    "volumes": [
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": data_pvc},
                        }
                    ],
                    "containers": [
                        {
                            "name": "portal-ui",
                            "image": image,
                            "command": command,
                            "args": args,
                            "env": env,
                            # Headless Chromium is CPU-bound; without a request
                            # the pod gets throttled and click actionability
                            # checks time out (clicks that pass locally fail
                            # in-cluster). Give it enough to drive the browser.
                            "resources": {
                                "requests": {"cpu": "1", "memory": "1Gi"},
                                "limits": {"memory": "3Gi"},
                            },
                            "volumeMounts": [
                                {"name": "data", "mountPath": f"{_HOME}/.tfactory"}
                            ],
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


def dispatch_all_portals(
    run_prefix: str, *, stagger_seconds: int = 40, **kwargs: Any
) -> list[str]:
    """Dispatch a portal-ui Job for EVERY portal at once, each Job self-staggering
    its Keycloak login by ``index * stagger_seconds`` so the same MFA user's
    one-time TOTP codes land in different 30s windows (no replay collision).

    Submits immediately (no blocking sleep in the caller) — the delay lives in
    each Job's command. Returns the Job names. ``stagger_seconds`` must exceed the
    TOTP period (30s); 40s is a safe default.
    """
    names: list[str] = []
    for i, portal in enumerate(PORTAL_KEYS):
        names.append(
            dispatch_portal_ui(
                portal,
                f"{run_prefix}-{portal}",
                startup_delay_seconds=i * stagger_seconds,
                **kwargs,
            )
        )
    return names

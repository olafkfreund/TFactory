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

import logging
import os
import shlex
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# The portals the capability knows about (kept in sync with config.PORTALS).
PORTAL_KEYS = ("pfactory", "aifactory", "tfactory", "cfactory")

# Fallback only. The cluster pins PORTAL_UI_IMAGE to an immutable
# `:sha-<short>` tag (factory-gitops, bumped by portal-ui-runner-image.yml), so
# what runs is identifiable by commit. `:latest` stays for ad-hoc local runs and
# is what this default resolves when the env var is unset -- but it must not be
# what the lane relies on: nothing built this image for five weeks and a stale
# `:latest` is indistinguishable from a current one, so the Job kept running the
# June harness that 502'd the portal it was testing (#886).
DEFAULT_IMAGE = "ghcr.io/olafkfreund/tfactory-runner-portal-ui:latest"
DEFAULT_NAMESPACE = "factory"
# Secret holding the enrolled MFA test user (provisioned by keycloak_provision).
DEFAULT_MFA_SECRET = "portal-ui-test-user"
_MFA_ENV = ("TEST_USER", "TEST_PASSWORD", "TEST_TOTP_SECRET")
# The control-plane data PVC carries the Visual Inspection store at
# ~/.tfactory/visual-inspections and the workspaces/*/specs/* the task list
# globs. The Job MUST co-mount the same claim the control plane mounts, or the
# harness runs, `publish()` and `publish_as_tfactory_spec()` both report
# success, and the run is invisible in both the Visual Reports tab and the task
# list -- written to a volume nothing reads, with no error to notice (#875).
# This constant is only the last resort: `resolve_data_pvc` asks the running
# control-plane pod first, so the Job follows the deployment instead of a
# guess that drifts the moment the claim is renamed.
DEFAULT_DATA_PVC = "tfactory-data-rwx"
_HOME = "/home/nonroot"
_DATA_MOUNT_PATH = f"{_HOME}/.tfactory"


def _claim_for_mount_path(pod: Any, mount_path: str) -> str | None:
    """The claimName of the volume a pod mounts at ``mount_path`` (pure).

    Takes the k8s pod object (or any object with the same attribute shape) so it
    is testable without a cluster.
    """
    spec = getattr(pod, "spec", None)
    if spec is None:
        return None
    names = {
        m.name
        for c in (spec.containers or [])
        for m in (c.volume_mounts or [])
        if m.mount_path == mount_path
    }
    for vol in spec.volumes or []:
        pvc = getattr(vol, "persistent_volume_claim", None)
        if vol.name in names and pvc is not None:
            return pvc.claim_name
    return None


def resolve_data_pvc() -> str:
    """The data claim the portal-ui Job should co-mount.

    Order: explicit ``TFACTORY_DATA_PVC`` env override, then the claim the
    running control-plane pod itself mounts at ``~/.tfactory``, then
    :data:`DEFAULT_DATA_PVC`. Reading it off the live pod is what keeps the two
    from drifting: the Job lands its evidence on whatever volume the reader is
    actually holding, in any environment, without this module having to know the
    claim's name. Best-effort -- outside a pod, or without ``pods get`` on the
    service account, it falls back to the constant.
    """
    override = os.environ.get("TFACTORY_DATA_PVC")
    if override:
        return override
    try:
        from kubernetes import client, config  # type: ignore

        config.load_incluster_config()
        pod_name = os.environ["HOSTNAME"]
        namespace = (
            Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
            .read_text()
            .strip()
        )
        pod = client.CoreV1Api().read_namespaced_pod(pod_name, namespace)
        claim = _claim_for_mount_path(pod, _DATA_MOUNT_PATH)
        if claim:
            return claim
        _log.warning(
            "portal-ui dispatch: control-plane pod %s mounts no PVC at %s; "
            "falling back to %s",
            pod_name,
            _DATA_MOUNT_PATH,
            DEFAULT_DATA_PVC,
        )
    except Exception as exc:  # noqa: BLE001 - not in a pod / no RBAC / no client
        _log.info(
            "portal-ui dispatch: cannot read the control-plane pod (%s); "
            "using default data PVC %s",
            exc,
            DEFAULT_DATA_PVC,
        )
    return DEFAULT_DATA_PVC


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
    data_pvc = data_pvc or resolve_data_pvc()
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
                # NOT app=tfactory: that is the `tfactory` Service's selector, so
                # the Job's pod joined the Service and, listening on nothing,
                # answered its share of real portal traffic with connection
                # refused -- Cloudflare served 502s for as long as the test ran.
                # The browser test took the portal it was testing offline and
                # then reported it broken.
                "metadata": {
                    "labels": {"app": "tfactory-portal-ui", "lane": "portal-ui"}
                },
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
                                {"name": "data", "mountPath": _DATA_MOUNT_PATH}
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

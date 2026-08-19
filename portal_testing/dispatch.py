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

The manifest's fleet-wide rules come from the vendored hub canonical
``tools.runners.job_dispatch`` (Factory#483/#638), not from literals here
(#908). Pod labels, Job-name sanitisation, and trace propagation are things
every factory Job must get right, and a comment saying so is not a mechanism —
before this the pod labels were correct *by comment*, the Job name was an
unsanitised f-string, and the Job carried none of the trace context the hub
adds. ``assert_job_policy`` checks the half a self-built manifest still owns,
and ``test_dispatch.py`` calls it, which is what the canonical asks consumers
to do.

The published runner image deliberately does NOT ship ``apps/backend``: it
vendors ``portal_testing/`` alone, and at runtime only ever runs
``portal_testing.run``, which never imports this module. The in-image test gate
mounts ``apps/backend/tools`` read-only for the duration, exactly as it already
mounts ``frameworks/``, so the #885 regressions keep executing against the
bytes that will run in the cluster.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_HUB_CANONICAL = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "backend"
    / "tools"
    / "runners"
    / "job_dispatch.py"
)


def _load_hub_canonical() -> Any:
    """Load the vendored hub Job canonical as a standalone module.

    Not ``from tools.runners.job_dispatch import ...``: ``tools/__init__.py``
    imports the backend's ToolExecutor, which pulls in ``providers`` and most of
    apps/backend. That resolves in the control plane and cannot in the harness
    image, which vendors ``portal_testing/`` alone — the packaging problem that
    left this builder governed by a comment (#908).

    ``job_dispatch.py`` imports nothing but the stdlib; it is written to be
    vendored. Loading it by path gets the canonical itself without dragging the
    package in, in both environments. Same file, one engine, no fork.

    Raises if it is missing. Falling back to literals is the exact drift this
    replaces — a Job built from a stale copy of the rules is worse than one that
    refuses to be built.
    """
    import importlib.util  # noqa: PLC0415 - only needed here

    spec = importlib.util.spec_from_file_location(
        "portal_testing._job_dispatch", _HUB_CANONICAL
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load the hub Job canonical at {_HUB_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_hub = _load_hub_canonical()
_short = _hub._short
assert_job_policy = _hub.assert_job_policy
job_labels = _hub.job_labels
task_pod_labels = _hub.task_pod_labels
trace_env = _hub.trace_env

__all__ = [
    "DEFAULT_DATA_PVC",
    "assert_job_policy",
    "build_portal_ui_job_manifest",
    "dispatch_all_portals",
    "dispatch_portal_ui",
    "portal_ui_job_name",
    "resolve_data_pvc",
]

# This lane's identity in the hub's vocabulary. `role` distinguishes dispatchers
# within one service: the nix lanes are `sandbox`, these are `portal-ui`. It is
# NOT the same axis as `factory.io/kind`, which stays "task" for every
# dispatcher because that is the label the per-task NetworkPolicy selects.
_SERVICE = "tfactory"
_ROLE = "portal-ui"

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


def _import_kubernetes_asyncio() -> Any:
    """Lazily import the (untyped, stub-less) ``kubernetes_asyncio`` package.

    Mirrors ``agents.verify_dispatch._import_kubernetes_asyncio``: the control
    plane image ships ``kubernetes_asyncio`` (async, matching the rest of the
    control plane), not the sync ``kubernetes`` client (#1001) -- the harness
    image needs neither, so the import stays lazy and this module stays
    importable without either package installed.
    """
    import importlib  # noqa: PLC0415 - lazy by design

    return importlib.import_module("kubernetes_asyncio")


async def _k8s_client_and_config() -> tuple[Any, Any]:
    """Load kube config (in-cluster, kubeconfig fallback) and return ``(k8s, api)``."""
    k8s = _import_kubernetes_asyncio()
    try:
        k8s.config.load_incluster_config()
    except Exception:  # noqa: BLE001 - dev/test fallback
        await k8s.config.load_kube_config()
    return k8s, k8s.client.ApiClient()


async def _read_control_plane_pod_claim(mount_path: str) -> str | None:
    """Ask the running control-plane pod which PVC it mounts at ``mount_path``."""
    k8s, api = await _k8s_client_and_config()
    try:
        pod_name = os.environ["HOSTNAME"]
        namespace = (
            Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
            .read_text()
            .strip()
        )
        pod = await k8s.client.CoreV1Api(api).read_namespaced_pod(pod_name, namespace)
        return _claim_for_mount_path(pod, mount_path)
    finally:
        await api.close()


def resolve_data_pvc() -> str:
    """The data claim the portal-ui Job should co-mount.

    Order: explicit ``TFACTORY_DATA_PVC`` env override, then the claim the
    running control-plane pod itself mounts at ``~/.tfactory``, then
    :data:`DEFAULT_DATA_PVC`. Reading it off the live pod is what keeps the two
    from drifting: the Job lands its evidence on whatever volume the reader is
    actually holding, in any environment, without this module having to know the
    claim's name. Best-effort -- outside a pod, or without ``pods get`` on the
    service account, it falls back to the constant.

    Stays a plain sync function (``asyncio.run`` bridges to the async
    ``kubernetes_asyncio`` client) so callers -- ``build_portal_ui_job_manifest``
    and the FastAPI route, both sync -- need no changes. Same bridge pattern as
    ``agents.verify_dispatch``'s blocking dispatch path.
    """
    override = os.environ.get("TFACTORY_DATA_PVC")
    if override:
        return override
    try:
        claim = asyncio.run(_read_control_plane_pod_claim(_DATA_MOUNT_PATH))
        if claim:
            return claim
        _log.warning(
            "portal-ui dispatch: control-plane pod mounts no PVC at %s; "
            "falling back to %s",
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
    """A DNS-1123 Job name for one portal's run.

    The run_id goes through the hub's ``_short`` rather than straight into an
    f-string: a run_id carrying ``_`` or any other non-DNS character yielded a
    name the API server rejects outright, and a 63-char truncation could leave a
    trailing ``-``. ``assert_job_policy`` fails a manifest for exactly that, and
    its error message names ``_short`` as the fix.
    """
    return f"portal-ui-{portal_key}-{_short(run_id)}".lower()[:63].strip("-")


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

    # Trace context, so a dispatched run continues the caller's trace instead of
    # it ending at the control plane (Factory#607/#638). Ambient by design: adds
    # nothing when this process is not exporting, which is every test run.
    env.extend(trace_env(_SERVICE))

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            # Job-OBJECT labels from the hub: `app` deliberately IS the service
            # here, because a Job object is never a Service endpoint.
            "labels": {
                **job_labels(_SERVICE, run_id),
                "lane": "portal-ui",
                "portal": portal_key,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            # A hung Playwright or Keycloak login had no k8s-enforced kill
            # deadline: ttlSecondsAfterFinished only starts once a Job finishes,
            # which a hung one never does. 45min is far past a real run (minutes)
            # including the staggered startup delay.
            "activeDeadlineSeconds": 2700,
            "template": {
                # From task_pod_labels, NOT literals. `app` must not be
                # `tfactory`: that is the Service's selector, so the Job's pod
                # joined the Service and, listening on nothing, answered its
                # share of real portal traffic with connection refused --
                # Cloudflare served 502s for as long as the test ran (#885).
                # That was right by comment; it is now right by construction.
                # It also gains factory.io/service and factory.io/kind=task --
                # the label the per-task NetworkPolicy selects, which these pods
                # never carried, so they ran under no policy at all.
                "metadata": {
                    "labels": {
                        **task_pod_labels(_SERVICE, role=_ROLE),
                        "lane": "portal-ui",
                        "portal": portal_key,
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "imagePullSecrets": [{"name": "ghcr-pull"}],
                    # The harness talks to public portals and a co-mounted PVC.
                    # It has no reason to hold a kube API token.
                    "automountServiceAccountToken": False,
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
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
                            # Chromium runs with --no-sandbox (see run.py), so
                            # it needs no added capabilities and must not be able
                            # to gain any. runAsNonRoot is deliberately NOT set:
                            # the MS Playwright base runs as root and its baked
                            # /ms-playwright + $HOME layout assume it, so pinning
                            # a uid here changes the harness's runtime, which
                            # this issue has no evidence for.
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
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


async def _create_job(manifest: dict[str, Any]) -> None:
    k8s, api = await _k8s_client_and_config()
    try:
        batch = k8s.client.BatchV1Api(api)
        await batch.create_namespaced_job(manifest["metadata"]["namespace"], manifest)
    finally:
        await api.close()


def dispatch_portal_ui(portal_key: str, run_id: str, **kwargs: Any) -> str:
    """Submit the portal-ui Job to the cluster. Returns the Job name.

    Best-effort: requires ``kubernetes_asyncio`` (the client the control-plane
    image ships, #1001 -- the sync ``kubernetes`` client is not in the image) +
    in-cluster config. Raises a clear error if unavailable (e.g. running outside
    a pod) so callers can fall back to a local ``python -m portal_testing.run``
    invocation.

    Stays a plain sync function -- ``asyncio.run`` bridges to the async client,
    same pattern as ``resolve_data_pvc`` and ``agents.verify_dispatch``'s
    blocking dispatch path -- so the FastAPI route above it needs no changes.
    """
    manifest = build_portal_ui_job_manifest(portal_key, run_id, **kwargs)
    try:
        _import_kubernetes_asyncio()
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "kubernetes client not available; run the harness locally"
        ) from e
    asyncio.run(_create_job(manifest))
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

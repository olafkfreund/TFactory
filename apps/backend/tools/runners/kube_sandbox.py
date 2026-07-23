"""Kubernetes Job-per-task sandbox — RFC-0005 Tier A execution substrate.

TFactory pods have no container runtime (k3d), so the hardened DockerRunner lanes
cannot spawn anything in-cluster. This backend launches a task's commands as an
ephemeral **Kubernetes Job** using the `tfactory-runner-nix` image, co-mounting
the project worktree from the workspaces PVC at `/work`, and running
`nix develop /work#default -c <commands>` there — the toolchain (incl. the
playwright browsers) comes from the per-task flake, not the image.

Ported from AIFactory's proven `core/kube_sandbox.py` (RFC-0005 #68). The
worktree co-mount relies on the Job landing on the same node as the TFactory pod
that holds the RWO workspaces PVC (true on the single-node k3d cluster).

`build_job_manifest()` is pure (no cluster / no client) and unit-tested; the
async lifecycle (create -> watch -> logs -> delete) uses `kubernetes_asyncio`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# TFactory's workspaces PVC is mounted here in the backend pod (charts/tfactory
# deployment.yaml); project clones live under workspaces/<name>.
_DEFAULT_DATA_ROOT = "/home/nonroot/.tfactory"

# ── Job-pod hardening (#651, Factory#274 compensating controls) ───────────────
#
# Pod-level: pin the RuntimeDefault seccomp profile (previously unset =
# Unconfined on most CRI defaults). `runAsNonRoot` is deliberately NOT set:
# the nix-runner image (nixos/nix) runs its builds as root by design — the
# co-mounted worktree files are uid 65532 (created by the control plane) and
# the warm-store seed copies the image's root-owned /nix — so forcing nonroot
# would break every nix lane. The root user is preserved; the capability
# add-backs below are what that root user actually needs against the
# uid-mismatched co-mount, everything else is dropped.
POD_SECURITY_CONTEXT: dict[str, Any] = {
    "seccompProfile": {"type": "RuntimeDefault"},
}
# Container-level: no privilege escalation, drop ALL capabilities, then add
# back only what the root nix user needs to work on the uid-65532 co-mount:
#   DAC_OVERRIDE — write the 65532-owned worktree at /work (junit/screenshots)
#   FOWNER       — chmod/utimes on 65532-owned files (git/tar/playwright)
#   CHOWN        — `cp -a` ownership preservation when seeding the warm store
#   SETUID/SETGID/KILL — #623: the image ships `build-users-group = nixbld`
#     (+32 nixbld users), so any LOCAL build makes nix setuid to a build user
#     and reap it. Without these, the moment a derivation cannot be substituted
#     from the binary cache, `nix develop` dies:
#         error: setting uid: Operation not permitted
#         error: cannot kill processes for uid '30001'
#     and the lane reports that downstream as a resolution failure. This is not
#     hypothetical: proven on the factory cluster against this same image with
#     exactly the previous add-back set (AIFactory#840/#841). The warm store
#     masks it — a Job that WINS the RWO mount race finds the closure prebuilt
#     and never builds; one that LOSES the race gets the image's cold /nix,
#     needs a local build, and hits this. That composition is the likeliest
#     explanation for this issue's intermittency: the mount race supplies the
#     "sometimes", these missing caps supply the failure.
# For the nonroot verify-orchestration Job these add-backs are inert (a
# non-root process gets no effective capabilities from them).
CONTAINER_SECURITY_CONTEXT: dict[str, Any] = {
    "allowPrivilegeEscalation": False,
    "privileged": False,
    "capabilities": {
        "drop": ["ALL"],
        "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL"],
    },
}


@dataclass
class JobRunResult:
    ok: bool
    exit_code: int
    output: str

    # RunResultLike conformance (agents/run_result.py, #426): expose the same
    # structural surface as DockerRunResult so the Nix-Job and Docker engines
    # return one shape. The Job collects combined pod logs into `output`, so
    # `stdout` mirrors it and `stderr` is empty (the stream split is not
    # preserved by the kubernetes log API used here).
    @property
    def returncode(self) -> int:
        return self.exit_code

    @property
    def stdout(self) -> str:
        return self.output

    @property
    def stderr(self) -> str:
        return ""


def _container_state(cs: Any) -> str:
    """One-word state for a (init)container status: waiting/running/terminated."""
    s = getattr(cs, "state", None)
    if s and getattr(s, "waiting", None):
        return f"waiting({s.waiting.reason})"
    if s and getattr(s, "running", None):
        return "running"
    if s and getattr(s, "terminated", None):
        return f"terminated(exit={s.terminated.exit_code},{s.terminated.reason})"
    return "unknown"


def _describe_pod(pod: Any) -> str:
    """A compact phase + per-container state summary for a diagnostic message."""
    st = getattr(pod, "status", None)
    if st is None:
        return "no pod status"
    parts = [f"phase={getattr(st, 'phase', '?')}"]
    for cs in st.init_container_statuses or []:
        parts.append(f"init/{cs.name}={_container_state(cs)}")
    for cs in st.container_statuses or []:
        parts.append(f"{cs.name}={_container_state(cs)}")
    return " ".join(parts)


def build_job_manifest(
    name: str,
    image: str,
    commands: list[str],
    *,
    namespace: str = "factory",
    image_pull_secret: str = "ghcr-pull",
    cpus: str = "2",
    memory: str = "4Gi",
    ttl_seconds: int = 180,
    timeout: int = 900,
    repo_pvc: str | None = None,
    repo_subpath: str | None = None,
    workdir: str = "/work",
    repo_ro: bool = False,
    network_none: bool = False,
    nix_store_pvc: str | None = None,
) -> dict:
    """Pure builder for the per-task Job manifest. No cluster access.

    When ``repo_pvc`` is given the worktree is co-mounted **rw** by default at
    ``workdir`` (the browser lane writes screenshots/junit into it, which
    TFactory collects after the Job). The browser lane needs egress to fetch the
    nixpkgs binary cache + reach the app, so ``network_none`` is False by default.

    When ``nix_store_pvc`` is given (RFC-0016 #197), the whole ``/nix`` tree is
    served from that warm-store PVC so per-task Nix lane Jobs stop cold-fetching
    the toolchain closure every run. ``/nix`` (not just ``/nix/store``) is mounted
    because the store and its sqlite db (``/nix/var/nix/db``) must stay
    consistent. An initContainer seeds the PVC from the image's own ``/nix`` on
    first use (when empty) so the nix binary's own closure survives the overlay.
    Omitted entirely when ``nix_store_pvc`` is None, leaving cold-fetch unchanged.
    """
    command = " && ".join(commands)
    # RFC-0016 (#465): carry explicit cpu/mem *requests* as well as *limits*. The
    # scheduler bin-packs on requests, so without them a fleet of verify Jobs all
    # land on one node and oversubscribe it (the limits alone don't reserve
    # capacity). requests == limits gives each lane a guaranteed reservation that
    # also caps it, so N concurrent verifies schedule across nodes instead of
    # piling up and OOMing.
    container: dict[str, Any] = {
        "name": "lane",
        "image": image,
        # #777: the runner image (535 MB) is already on any warm node — the k8s
        # default of Always for a `:latest`/mutable tag re-pulled it (~11.5s) on
        # EVERY Job. IfNotPresent makes the node cache authoritative; when the
        # image is dispatched by its immutable `:sha-<short>` tag this is also
        # always-correct (a new build gets a new tag, so no staleness).
        "imagePullPolicy": "IfNotPresent",
        "command": ["bash", "-c", command],
        "resources": {
            "requests": {"cpu": cpus, "memory": memory},
            "limits": {"cpu": cpus, "memory": memory},
        },
        "securityContext": dict(CONTAINER_SECURITY_CONTEXT),
    }
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,  # the lane needs no k8s API
        "imagePullSecrets": [{"name": image_pull_secret}],
        "securityContext": dict(POD_SECURITY_CONTEXT),
        "containers": [container],
    }
    volumes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    if repo_pvc:
        container["workingDir"] = workdir
        mounts.append(
            {
                "name": "repo",
                "mountPath": workdir,
                "subPath": repo_subpath,
                "readOnly": repo_ro,
            }
        )
        volumes.append(
            {
                "name": "repo",
                "persistentVolumeClaim": {"claimName": repo_pvc, "readOnly": repo_ro},
            }
        )
    if nix_store_pvc:
        mounts.append({"name": "nix-store", "mountPath": "/nix"})
        volumes.append(
            {
                "name": "nix-store",
                "persistentVolumeClaim": {"claimName": nix_store_pvc},
            }
        )
        pod_spec["initContainers"] = [
            {
                "name": "seed-nix-store",
                "image": image,
                "imagePullPolicy": "IfNotPresent",  # #777: reuse the node cache
                "command": [
                    "sh",
                    "-c",
                    "if [ ! -e /warm/store ]; then "
                    "cp -a /nix/. /warm/ && echo 'seeded warm nix store'; "
                    "else echo 'warm nix store already populated'; fi",
                ],
                "volumeMounts": [{"name": "nix-store", "mountPath": "/warm"}],
                "securityContext": dict(CONTAINER_SECURITY_CONTEXT),
            }
        ]
    if mounts:
        container["volumeMounts"] = mounts
    if volumes:
        pod_spec["volumes"] = volumes
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "tfactory-sandbox"},
        },
        "spec": {
            "ttlSecondsAfterFinished": ttl_seconds,
            "backoffLimit": 0,
            "activeDeadlineSeconds": timeout,
            "template": {
                "metadata": {"labels": {"app": "tfactory-sandbox", "job-name": name}},
                "spec": pod_spec,
            },
        },
    }


def pvc_subpath(workdir: str | None, data_root: str = _DEFAULT_DATA_ROOT) -> str | None:
    """PVC-relative subPath for an absolute ``workdir``, or None when outside it."""
    if not workdir:
        return None
    root = data_root.rstrip("/") + "/"
    norm = workdir.rstrip("/")
    if norm == data_root.rstrip("/"):
        return ""
    if not norm.startswith(root):
        return None
    return norm[len(root) :]


class KubeJobSandbox:
    def __init__(
        self,
        image: str,
        *,
        namespace: str = "factory",
        repo_pvc: str | None = None,
        data_root: str = _DEFAULT_DATA_ROOT,
        **manifest_kw,
    ):
        self.image = image
        self.namespace = namespace
        self.repo_pvc = repo_pvc
        self.data_root = data_root
        self.manifest_kw = manifest_kw

    async def _run_async(
        self, commands: list[str], timeout: int, workdir: str | None
    ) -> JobRunResult:
        from kubernetes_asyncio import client, config

        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001 - dev/test fallback
            await config.load_kube_config()

        name = "tfsbx-" + uuid.uuid4().hex[:10]
        repo_kw: dict = {}
        if self.repo_pvc:
            subpath = pvc_subpath(workdir, self.data_root)
            if subpath is not None:
                repo_kw = {"repo_pvc": self.repo_pvc, "repo_subpath": subpath}
            else:
                logger.info(
                    "[kube-sandbox] workdir %r outside data root %r; toolchain-only",
                    workdir,
                    self.data_root,
                )
        manifest = build_job_manifest(
            name,
            self.image,
            commands,
            namespace=self.namespace,
            timeout=timeout,
            **repo_kw,
            **self.manifest_kw,
        )
        api = client.ApiClient()
        batch, core = client.BatchV1Api(api), client.CoreV1Api(api)
        try:
            await batch.create_namespaced_job(self.namespace, manifest)
            succeeded = False
            terminal = False
            for _ in range(max(1, timeout // 3)):
                st = (await batch.read_namespaced_job(name, self.namespace)).status
                if st and st.succeeded:
                    succeeded = terminal = True
                    break
                if st and st.failed:
                    terminal = True
                    break
                await asyncio.sleep(3)
            pods = await core.list_namespaced_pod(
                self.namespace, label_selector=f"job-name={name}"
            )
            pod = pods.items[0] if pods.items else None
            output = ""
            if pod is not None:
                try:
                    # Read the work container explicitly — the pod also has a
                    # seed-nix-store initContainer, and the default target can be
                    # the wrong / not-yet-started container on a slow Nix build.
                    output = await core.read_namespaced_pod_log(
                        pod.metadata.name, self.namespace, container="lane"
                    )
                except Exception as exc:  # noqa: BLE001
                    output = f"(log unavailable: {exc})"
            output = (output or "").strip()
            # Never return a silent empty log (#621): a build that outran the wait
            # budget, or a lane container still starting, must surface as an env
            # diagnostic so the caller marks the lane errored rather than grading a
            # passing test as a failure.
            if not terminal or not output:
                note = (
                    "job did not reach a terminal state within the wait budget"
                    if not terminal
                    else "empty lane log"
                )
                diag = _describe_pod(pod) if pod is not None else "no pod created"
                output = f"{output}\n[kube-sandbox] {note}; {diag}".strip()
            return JobRunResult(succeeded, 0 if succeeded else 1, output)
        finally:
            try:
                await batch.delete_namespaced_job(
                    name, self.namespace, propagation_policy="Background"
                )
            except Exception:  # noqa: BLE001 - ttlSecondsAfterFinished GCs anyway
                pass
            await api.close()

    def run(
        self, commands: list[str], *, workdir: str | None = None, timeout: int = 900
    ) -> JobRunResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — the simple synchronous case.
            return asyncio.run(self._run_async(commands, timeout, workdir))
        # Called from WITHIN a running event loop (the async verify evaluator runs
        # this Nix lane). ``asyncio.run()`` would raise "cannot be called from a
        # running event loop", which the caller swallows into a silent host
        # fallback — so the Nix lane never dispatched in the kubejob path. Run the
        # coroutine to completion on a dedicated thread with its own loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(self._run_async(commands, timeout, workdir))
            ).result()

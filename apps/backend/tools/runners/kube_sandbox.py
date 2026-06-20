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
import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# TFactory's workspaces PVC is mounted here in the backend pod (charts/tfactory
# deployment.yaml); project clones live under workspaces/<name>.
_DEFAULT_DATA_ROOT = "/home/nonroot/.tfactory"


@dataclass
class JobRunResult:
    ok: bool
    exit_code: int
    output: str


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
) -> dict:
    """Pure builder for the per-task Job manifest. No cluster access.

    When ``repo_pvc`` is given the worktree is co-mounted **rw** by default at
    ``workdir`` (the browser lane writes screenshots/junit into it, which
    TFactory collects after the Job). The browser lane needs egress to fetch the
    nixpkgs binary cache + reach the app, so ``network_none`` is False by default.
    """
    command = " && ".join(commands)
    container = {
        "name": "lane",
        "image": image,
        "command": ["bash", "-c", command],
        "resources": {"limits": {"cpu": cpus, "memory": memory}},
    }
    pod_spec: dict = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,  # the lane needs no k8s API
        "imagePullSecrets": [{"name": image_pull_secret}],
        "containers": [container],
    }
    if repo_pvc:
        container["workingDir"] = workdir
        container["volumeMounts"] = [
            {
                "name": "repo",
                "mountPath": workdir,
                "subPath": repo_subpath,
                "readOnly": repo_ro,
            }
        ]
        pod_spec["volumes"] = [
            {
                "name": "repo",
                "persistentVolumeClaim": {"claimName": repo_pvc, "readOnly": repo_ro},
            }
        ]
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
            for _ in range(max(1, timeout // 3)):
                st = (await batch.read_namespaced_job(name, self.namespace)).status
                if st and st.succeeded:
                    succeeded = True
                    break
                if st and st.failed:
                    break
                await asyncio.sleep(3)
            pods = await core.list_namespaced_pod(
                self.namespace, label_selector=f"job-name={name}"
            )
            output = ""
            if pods.items:
                try:
                    output = await core.read_namespaced_pod_log(
                        pods.items[0].metadata.name, self.namespace
                    )
                except Exception as exc:  # noqa: BLE001
                    output = f"(log unavailable: {exc})"
            return JobRunResult(
                succeeded, 0 if succeeded else 1, (output or "").strip()
            )
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
        return asyncio.run(self._run_async(commands, timeout, workdir))

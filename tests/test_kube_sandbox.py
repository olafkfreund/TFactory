"""RFC-0005 Tier A: k8s Job manifest builder for the Nix lane (pure, no cluster)."""

from __future__ import annotations

import asyncio

from tools.runners.kube_sandbox import (
    JobRunResult,
    KubeJobSandbox,
    build_job_manifest,
    pvc_subpath,
)


def _sandbox_with_fake_run() -> KubeJobSandbox:
    sb = KubeJobSandbox("img", namespace="factory")

    async def _fake(commands, timeout, workdir):
        return JobRunResult(ok=True, exit_code=0, output="hi")

    sb._run_async = _fake  # type: ignore[method-assign]
    return sb


def test_run_works_without_a_running_loop():
    res = _sandbox_with_fake_run().run(["echo hi"])
    assert res.ok and res.exit_code == 0 and res.output == "hi"


def test_run_works_inside_a_running_event_loop():
    # The async verify evaluator calls sandbox.run() from within a running loop.
    # asyncio.run() would raise "cannot be called from a running event loop" there
    # (which the caller swallowed into a silent host fallback), so run() must
    # offload to a dedicated thread. Regression guard for the Nix-lane dispatch.
    sb = _sandbox_with_fake_run()

    async def caller():
        return sb.run(["echo hi"])

    res = asyncio.run(caller())
    assert res.ok and res.output == "hi"


def test_toolchain_only_job_has_no_volume():
    m = build_job_manifest("j1", "img:latest", ["nix --version"])
    spec = m["spec"]["template"]["spec"]
    assert m["kind"] == "Job"
    assert spec["containers"][0]["command"] == ["bash", "-c", "nix --version"]
    assert "volumes" not in spec
    assert spec["automountServiceAccountToken"] is False
    assert m["spec"]["backoffLimit"] == 0
    assert m["metadata"]["labels"]["app"] == "tfactory-sandbox"


def test_repo_comount_rw_for_browser_lane():
    m = build_job_manifest(
        "j2",
        "img",
        ["nix develop /work#default -c playwright test"],
        repo_pvc="tf-workspaces",
        repo_subpath="workspaces/proj",
    )
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["workingDir"] == "/work"
    vm = c["volumeMounts"][0]
    assert vm["mountPath"] == "/work" and vm["subPath"] == "workspaces/proj"
    assert vm["readOnly"] is False  # browser lane writes screenshots back
    vol = m["spec"]["template"]["spec"]["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "tf-workspaces"


def test_pvc_subpath():
    root = "/home/nonroot/.tfactory"
    assert pvc_subpath(f"{root}/workspaces/proj", root) == "workspaces/proj"
    assert pvc_subpath(root, root) == ""
    assert pvc_subpath("/somewhere/else", root) is None
    assert pvc_subpath(None, root) is None


def test_timeout_and_ttl_passthrough():
    m = build_job_manifest("j3", "img", ["true"], timeout=600, ttl_seconds=90)
    assert m["spec"]["activeDeadlineSeconds"] == 600
    assert m["spec"]["ttlSecondsAfterFinished"] == 90


def test_resources_carry_requests_and_limits():
    # RFC-0016 #465: the scheduler bin-packs on requests, so the Job must carry
    # explicit cpu/mem *requests* (== limits) — not limits alone — or a fleet of
    # verify Jobs piles onto one node and oversubscribes it.
    m = build_job_manifest("jr", "img", ["true"], cpus="3", memory="6Gi")
    res = m["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["requests"] == {"cpu": "3", "memory": "6Gi"}
    assert res["limits"] == {"cpu": "3", "memory": "6Gi"}


def test_resources_default_requests_present():
    m = build_job_manifest("jr2", "img", ["true"])
    res = m["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["requests"]["cpu"] == "2"
    assert res["requests"]["memory"] == "4Gi"


def test_no_warm_nix_store_by_default():
    # RFC-0016 #197: cold behavior unchanged when no warm-store PVC named.
    m = build_job_manifest("j4", "img", ["nix --version"])
    t = m["spec"]["template"]["spec"]
    assert "initContainers" not in t
    assert "volumes" not in t


def test_warm_nix_store_mounted_with_seed_init():
    # RFC-0016 #197: whole /nix served from the warm-store PVC + seed initContainer.
    m = build_job_manifest(
        "j5",
        "ghcr.io/olafkfreund/tfactory-runner-nix:latest",
        ["nix develop path:/work#default -c cargo test"],
        repo_pvc="tfactory-data",
        repo_subpath="ws/proj",
        nix_store_pvc="tfactory-nix-store",
    )
    t = m["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in t["volumes"]}
    assert (
        vols["nix-store"]["persistentVolumeClaim"]["claimName"] == "tfactory-nix-store"
    )
    assert "repo" in vols
    mounts = {vm["name"]: vm for vm in t["containers"][0]["volumeMounts"]}
    assert mounts["nix-store"]["mountPath"] == "/nix"
    init = t["initContainers"][0]
    assert init["name"] == "seed-nix-store"
    assert init["volumeMounts"][0]["mountPath"] == "/warm"
    assert "/warm/store" in init["command"][-1]


def test_job_pod_and_container_are_hardened():
    # #651 (Factory#274 compensating controls): seccomp RuntimeDefault pinned on
    # the pod; no privilege escalation + drop ALL on every container. The root
    # nix user keeps only the co-mount add-backs (uid-65532 worktree writes +
    # warm-store seeding); runAsNonRoot is deliberately NOT set — the nix-runner
    # image builds as root by design.
    m = build_job_manifest(
        "jh",
        "img",
        ["true"],
        repo_pvc="tfactory-data",
        repo_subpath="ws/p",
        nix_store_pvc="tfactory-nix-store",
    )
    pod = m["spec"]["template"]["spec"]
    assert pod["securityContext"] == {"seccompProfile": {"type": "RuntimeDefault"}}
    assert "runAsNonRoot" not in pod["securityContext"]
    for c in [*pod["containers"], *pod.get("initContainers", [])]:
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["privileged"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        assert set(sc["capabilities"]["add"]) == {"CHOWN", "DAC_OVERRIDE", "FOWNER"}


def test_container_state_variants():
    from types import SimpleNamespace as NS

    from tools.runners.kube_sandbox import _container_state

    assert (
        _container_state(NS(state=NS(waiting=NS(reason="PodInitializing"))))
        == "waiting(PodInitializing)"
    )
    assert _container_state(NS(state=NS(waiting=None, running=object()))) == "running"
    assert (
        _container_state(
            NS(
                state=NS(
                    waiting=None,
                    running=None,
                    terminated=NS(exit_code=1, reason="Error"),
                )
            )
        )
        == "terminated(exit=1,Error)"
    )


def test_describe_pod_summarizes_phase_and_containers():
    from types import SimpleNamespace as NS

    from tools.runners.kube_sandbox import _describe_pod

    pod = NS(
        status=NS(
            phase="Running",
            init_container_statuses=[
                NS(name="seed-nix-store", state=NS(waiting=None, running=object()))
            ],
            container_statuses=[
                NS(name="lane", state=NS(waiting=NS(reason="PodInitializing")))
            ],
        )
    )
    d = _describe_pod(pod)
    assert "phase=Running" in d
    assert "init/seed-nix-store=running" in d
    assert "lane=waiting(PodInitializing)" in d

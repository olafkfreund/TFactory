"""RFC-0005 Tier A: k8s Job manifest builder for the Nix lane (pure, no cluster)."""

from __future__ import annotations

import asyncio

from tools.runners.job_dispatch import assert_job_policy
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
        assert set(sc["capabilities"]["add"]) == {
            "CHOWN",
            "DAC_OVERRIDE",
            "FOWNER",
            "SETUID",
            "SETGID",
            "KILL",
        }


def test_nix_local_builds_keep_their_build_user_caps():
    """#623: the runner image ships `build-users-group = nixbld`, so any LOCAL
    build makes nix setuid to a build user and reap it. Without SETUID/SETGID/
    KILL, `nix develop` dies the moment a derivation cannot be substituted:

        error: setting uid: Operation not permitted
        error: cannot kill processes for uid '30001'

    Reproduced on the factory cluster with this exact image and the previous
    add-back set, then fixed by adding these three (AIFactory#840/#841 hit the
    same wall on the same image).

    The warm store MASKS this: a Job that wins the RWO /nix mount race finds the
    closure prebuilt and never builds; one that loses the race gets a cold /nix
    and needs a local build. That composition is the likeliest explanation for
    #623's intermittency — so these caps must survive any future de-pin, which
    removes the warm store and makes local builds the normal case.
    """
    m = build_job_manifest("jh", "img", ["true"])
    for c in m["spec"]["template"]["spec"]["containers"]:
        add = set(c["securityContext"]["capabilities"]["add"])
        assert {"SETUID", "SETGID", "KILL"} <= add, add


def test_image_pull_policy_reuses_node_cache():
    # #777: the 535 MB runner image is already on any warm node — a default of
    # Always re-pulled it on every Job (~11.5s). IfNotPresent must be pinned on
    # the lane AND the seed-nix init container so the node cache is authoritative.
    m = build_job_manifest(
        "jp",
        "img",
        ["true"],
        repo_pvc="tfactory-data",
        repo_subpath="ws/p",
        nix_store_pvc="tfactory-nix-store",
    )
    pod = m["spec"]["template"]["spec"]
    for c in [*pod["containers"], *pod.get("initContainers", [])]:
        assert c["imagePullPolicy"] == "IfNotPresent"


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


# ── deploy dry-run service account (#603) ───────────────────────────────────


def test_service_account_opt_in_mounts_token():
    """The deploy lane's kubectl --dry-run=server needs a scoped SA token; passing
    service_account sets it AND flips automount on (verify lanes never do)."""
    m = build_job_manifest(
        "jsa",
        "img",
        ["kubectl apply --dry-run=server -f ."],
        service_account="tfactory-deploy-dryrun",
    )
    spec = m["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == "tfactory-deploy-dryrun"
    assert spec["automountServiceAccountToken"] is True


def test_no_service_account_keeps_token_unmounted():
    m = build_job_manifest("jns", "img", ["true"])
    spec = m["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in spec


def test_with_manifest_kw_merges_without_mutating_original():
    base = KubeJobSandbox("img", namespace="factory", network_none=True)
    deploy = base.with_manifest_kw(service_account="sa-x", network_none=False)
    assert deploy.manifest_kw["service_account"] == "sa-x"
    assert deploy.manifest_kw["network_none"] is False
    # original untouched
    assert "service_account" not in base.manifest_kw
    assert base.manifest_kw["network_none"] is True


# ── The shared job-dispatch contract (Factory#483) ────────────────────────────
#
# This builder predates the hub's scripts/job_dispatch.py and restated its rules
# by hand. assert_job_policy is the hub's own checker, vendored byte-exact, so
# these are not TFactory's opinion of the rules - they are the rules, and a
# change to them lands here through a re-vendor rather than through a hand-edit.


def test_lane_job_obeys_the_shared_job_policy():
    m = build_job_manifest(
        "j1",
        "img",
        ["true"],
        repo_pvc="tfactory-data",
        repo_subpath="ws/p",
        nix_store_pvc="tfactory-nix-store",
    )
    assert_job_policy(m)


def test_lane_pod_is_not_selectable_as_a_tfactory_service_backend():
    # The `tfactory` Service selects exactly {"app": "tfactory"} and a Service
    # selector is a SUBSET match, so a Job pod carrying that label joins it as an
    # endpoint, listens on nothing, and answers real traffic with connection
    # refused. That is TFactory#885 (the portal-ui lane took the portal offline
    # and then reported it broken), AIFactory#1107, and Factory#458 - three
    # independent arrivals of one defect.
    m = build_job_manifest("j1", "img", ["true"])
    pod_labels = m["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["app"] != "tfactory"
    # Unchanged and load-bearing: charts/tfactory/templates/networkpolicy-jobs.yaml
    # selects exactly this value, so the shared helper must keep producing it.
    assert pod_labels["app"] == "tfactory-sandbox"
    assert pod_labels["factory.io/service"] == "tfactory"
    # #812: the per-task NetworkPolicy selector. This builder never set it.
    assert pod_labels["factory.io/kind"] == "task"
    # The Job OBJECT keeps app=tfactory-sandbox: a Job is never a Service endpoint
    # and never a `kubectl exec` target, so label queries on it stay useful.
    assert m["metadata"]["labels"]["app"] == "tfactory-sandbox"


def test_job_name_stays_a_valid_dns_label():
    # kube_sandbox names Jobs tfsbx-<uuid10>, not factory-<service>-<short>. The
    # PREFIX is deliberately per-service (see the hub docstring); DNS-1123
    # VALIDITY is not, and assert_job_policy checks that for whatever scheme a
    # consumer picks.
    assert_job_policy(build_job_manifest("j1", "img", ["true"]))


# ── RFC-0017 #190: packed workspace, no PVC, node-agnostic ────────────────────
#
# The repo_pvc co-mount is RWO, so it pins a verify Job to whichever node holds
# the worktree. That pin is the single reason this fleet cannot verify across
# nodes. These pin the packed alternative.


def test_packed_workspace_uses_emptydir_and_no_pvc():
    m = build_job_manifest(
        "j-packed",
        "img",
        ["nix develop /work#default -c playwright test"],
        workspace_uri="s3://factory-artifacts/tfactory/1/spec/workspace.tar.gz",
    )
    spec = m["spec"]["template"]["spec"]
    repo = [v for v in spec["volumes"] if v["name"] == "repo"]
    assert len(repo) == 1, spec["volumes"]
    assert repo[0] == {"name": "repo", "emptyDir": {}}, repo[0]
    # No PVC anywhere — a PVC is exactly what re-pins the Job to a node.
    assert all("persistentVolumeClaim" not in v for v in spec["volumes"]), spec[
        "volumes"
    ]
    c = spec["containers"][0]
    assert c["workingDir"] == "/work"
    env = {e["name"]: e["value"] for e in c.get("env", [])}
    assert env["WORKSPACE_URI"].endswith("workspace.tar.gz"), env


def test_packed_workspace_wins_over_repo_pvc():
    """A caller mid-migration that still threads its PVC must get the packed
    behaviour, not silently re-pin itself to a node. Passing both is a migration
    state, not an error — and the failure mode it guards against is invisible:
    the Job runs fine, it just cannot schedule anywhere else."""
    m = build_job_manifest(
        "j-both",
        "img",
        ["true"],
        repo_pvc="tfactory-data",
        repo_subpath="workspaces/x",
        workspace_uri="s3://bucket/w.tar.gz",
    )
    spec = m["spec"]["template"]["spec"]
    assert all("persistentVolumeClaim" not in v for v in spec["volumes"]), spec[
        "volumes"
    ]
    repo = [v for v in spec["volumes"] if v["name"] == "repo"]
    assert repo[0] == {"name": "repo", "emptyDir": {}}, repo[0]
    mounts = spec["containers"][0]["volumeMounts"]
    repo_mt = [m2 for m2 in mounts if m2["name"] == "repo"]
    # subPath belongs to the PVC path; the packed mount must not carry it.
    assert "subPath" not in repo_mt[0], repo_mt[0]


def test_no_workspace_uri_keeps_the_pvc_path_unchanged():
    """The default must be byte-identical to before, or this change is a
    regression for every existing caller."""
    m = build_job_manifest(
        "j-pvc", "img", ["true"], repo_pvc="tfactory-data", repo_subpath="workspaces/x"
    )
    spec = m["spec"]["template"]["spec"]
    repo = [v for v in spec["volumes"] if v["name"] == "repo"]
    assert repo[0]["persistentVolumeClaim"]["claimName"] == "tfactory-data", repo[0]
    env = {e["name"]: e["value"] for e in spec["containers"][0].get("env", [])}
    assert "WORKSPACE_URI" not in env, env

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
    # Raised from 4Gi: an uncached nix derivation OOM-killed inside the old cap
    # while the node sat at 4% of 251Gi (spec 190).
    assert res["requests"]["memory"] == "8Gi"


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
        "ghcr.io/olafkfreund/factory-runner-nix:latest",
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


# ── TFactory#1152 fallout: a finished Job's logs outlive the investigation ─────


def test_ttl_defaults_unchanged_when_the_env_is_unset(monkeypatch):
    from tools.runners.kube_sandbox import _TTL_DEFAULT, _resolve_ttl

    monkeypatch.delenv("TFACTORY_SANDBOX_JOB_TTL", raising=False)
    assert _resolve_ttl(None) == _TTL_DEFAULT


def test_the_env_raises_the_ttl(monkeypatch):
    """Why this is configurable at all: #1152's sandbox Jobs failed with
    BackoffLimitExceeded and were garbage-collected before anyone could read
    why, so three investigations had only stability=error to work from."""
    from tools.runners.kube_sandbox import _resolve_ttl

    monkeypatch.setenv("TFACTORY_SANDBOX_JOB_TTL", "3600")
    assert _resolve_ttl(None) == 3600


def test_an_explicit_argument_still_wins_over_the_env(monkeypatch):
    from tools.runners.kube_sandbox import _resolve_ttl

    monkeypatch.setenv("TFACTORY_SANDBOX_JOB_TTL", "3600")
    assert _resolve_ttl(30) == 30


def test_a_junk_value_falls_back_instead_of_raising(monkeypatch):
    """A bad env var must not take the verify lane down."""
    from tools.runners.kube_sandbox import _TTL_DEFAULT, _resolve_ttl

    monkeypatch.setenv("TFACTORY_SANDBOX_JOB_TTL", "soon")
    assert _resolve_ttl(None) == _TTL_DEFAULT


def test_zero_does_not_become_delete_immediately(monkeypatch):
    """Kubernetes reads ttlSecondsAfterFinished=0 as 'delete the moment it
    finishes', which would destroy the very evidence this exists to keep. A
    non-positive value is a mistake, not an instruction."""
    from tools.runners.kube_sandbox import _TTL_DEFAULT, _resolve_ttl

    for junk in ("0", "-1"):
        monkeypatch.setenv("TFACTORY_SANDBOX_JOB_TTL", junk)
        assert _resolve_ttl(None) == _TTL_DEFAULT


def test_the_resolved_ttl_reaches_the_manifest(monkeypatch):
    """The wiring, not just the resolver — a value that never reaches
    ttlSecondsAfterFinished changes nothing about how long logs survive."""
    from tools.runners.kube_sandbox import build_job_manifest

    monkeypatch.setenv("TFACTORY_SANDBOX_JOB_TTL", "1800")
    manifest = build_job_manifest("tfsbx-x", "img:tag", ["true"])
    assert manifest["spec"]["ttlSecondsAfterFinished"] == 1800


# ─── #1391: packed verify — nested lanes get their workdir by object store ────


class _LaneS3:
    """In-memory boto3 S3 client: just what the packed lane path calls."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_extra):  # noqa: N803 - boto3 names
        self.objects[Key] = Body

    def get_object(self, *, Bucket, Key):  # noqa: N803 - boto3 names
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):  # noqa: N803 - boto3 names
        self.objects.pop(Key, None)

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 - boto3 names
        return f"https://signed/{op}/{Params['Key']}"


def _fake_kube(monkeypatch, s3: _LaneS3, created: list[dict]):
    """Fake kubernetes_asyncio whose Job 'runs' the lane: it reads the packed
    workdir, writes a junit file into it, and PUTs the tree to the out URL."""
    import io
    import sys
    import tarfile
    import types

    from tools.runners.artifact_store import _safe_extract

    class _Batch:
        async def create_namespaced_job(self, ns, manifest):
            created.append(manifest)
            env = {
                e["name"]: e["value"]
                for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]
            }
            in_key = env["TF_LANE_IN_URL"].split("/get_object/", 1)[1]
            out_key = env["TF_LANE_OUT_URL"].split("/put_object/", 1)[1]
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as d:
                _safe_extract(s3.objects[in_key], Path(d))
                assert (Path(d) / "sut.py").read_text() == "x = 1\n"
                stage = Path(d) / ".tf_pytest"
                stage.mkdir()
                (stage / "junit.xml").write_text("<testsuite/>")
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    tar.add(d, arcname=".")
                s3.objects[out_key] = buf.getvalue()

        async def read_namespaced_job(self, name, ns):
            return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=1))

        async def delete_namespaced_job(self, *a, **k):
            return None

    class _Core:
        async def list_namespaced_pod(self, *a, **k):
            return types.SimpleNamespace(items=[])

    class _Api:
        async def close(self):
            return None

    async def _load_kube_config():
        return None

    def _incluster():
        raise RuntimeError("not in cluster")

    mod = types.ModuleType("kubernetes_asyncio")
    mod.client = types.SimpleNamespace(
        ApiClient=_Api, BatchV1Api=lambda api: _Batch(), CoreV1Api=lambda api: _Core()
    )
    mod.config = types.SimpleNamespace(
        load_incluster_config=_incluster, load_kube_config=_load_kube_config
    )
    monkeypatch.setitem(sys.modules, "kubernetes_asyncio", mod)


def test_packed_lane_has_no_pvc_and_collects_results(monkeypatch, tmp_path):
    from tools.runners.artifact_store import ArtifactStore

    s3 = _LaneS3()
    monkeypatch.setenv("S3_ENDPOINT", "http://minio.invalid:9000")
    monkeypatch.setattr(ArtifactStore, "_client", lambda self: s3)
    created: list[dict] = []
    _fake_kube(monkeypatch, s3, created)
    workdir = tmp_path / "_nixrun-abc"
    workdir.mkdir()
    (workdir / "sut.py").write_text("x = 1\n")

    sb = KubeJobSandbox("img", repo_pvc=None, pack_workdir=True)
    res = sb.run(["pytest -q"], workdir=str(workdir), timeout=6)

    assert res.ok, res.output
    pod = created[0]["spec"]["template"]["spec"]
    assert all("persistentVolumeClaim" not in v for v in pod.get("volumes", []))
    assert {"name": "repo", "emptyDir": {}} in pod["volumes"]
    cmd = pod["containers"][0]["command"][2]
    assert "( pytest -q )" in cmd and "exit $rc" in cmd
    # the lane's results came back to where the caller reads them
    assert (workdir / ".tf_pytest" / "junit.xml").read_text() == "<testsuite/>"
    # both short-lived lane archives are cleaned up
    assert s3.objects == {}


def test_packed_lane_without_object_store_fails_without_a_job(monkeypatch, tmp_path):
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    sb = KubeJobSandbox("img", pack_workdir=True)
    res = sb.run(["true"], workdir=str(tmp_path), timeout=6)
    assert not res.ok and "lane pack failed" in res.output


def test_unpacked_sandbox_keeps_pvc_co_mount_and_no_pack():
    sb = KubeJobSandbox("img", repo_pvc="tfactory-data")
    assert sb.pack_workdir is False
    assert sb.with_manifest_kw(service_account="x").repo_pvc == "tfactory-data"


# ─── #1393: packed lanes — not collected, leaked objects, push-back budget ────


def test_uncollected_lane_is_never_a_pass_and_classifies_as_environment():
    from agents.nix_env import _parse_exit_marker, parse_pytest_exits
    from agents.stability_runner import classify_pytest_failure
    from tools.runners.kube_sandbox import _collected

    def _lost() -> None:
        raise RuntimeError("no such key")

    out = "__PYTEST_RUN=0\n1 passed\n__PYTEST_EXIT=0\n__GOTEST_EXIT=0\n__DEPLOY_STEP_0_EXIT=0"
    res = _collected(_lost, True, out)
    assert not res.ok
    assert _parse_exit_marker(res.output, "__PYTEST_EXIT=") == 1
    assert _parse_exit_marker(res.output, "__GOTEST_EXIT=") == 1
    assert _parse_exit_marker(res.output, "__DEPLOY_STEP_0_EXIT=") == 1
    assert [code for code, _ in parse_pytest_exits(res.output)] == [1]
    assert classify_pytest_failure(res.output, 1) == "environment"


def test_packed_lane_objects_are_deleted_when_job_create_raises(monkeypatch, tmp_path):
    from tools.runners.artifact_store import ArtifactStore

    s3 = _LaneS3()
    monkeypatch.setenv("S3_ENDPOINT", "http://minio.invalid:9000")
    monkeypatch.setattr(ArtifactStore, "_client", lambda self: s3)
    created: list[dict] = []
    _fake_kube(monkeypatch, s3, created)
    import sys

    batch_cls = sys.modules["kubernetes_asyncio"].client.BatchV1Api(None).__class__

    async def _boom(self, ns, manifest):
        env = manifest["spec"]["template"]["spec"]["containers"][0]["env"]
        url = next(e["value"] for e in env if e["name"] == "TF_LANE_OUT_URL")
        s3.objects[url.split("/put_object/", 1)[1]] = b"partial push-back"
        raise RuntimeError("apiserver down")

    monkeypatch.setattr(batch_cls, "create_namespaced_job", _boom)
    (tmp_path / "sut.py").write_text("x = 1\n")

    sb = KubeJobSandbox("img", repo_pvc=None, pack_workdir=True)
    import pytest

    with pytest.raises(RuntimeError, match="apiserver down"):
        sb.run(["pytest -q"], workdir=str(tmp_path), timeout=6)
    # both the input archive (the SUT) and the partial output are gone
    assert s3.objects == {}


def test_packed_lane_reserves_push_back_time_and_tags_output(monkeypatch, tmp_path):
    from tools.runners.artifact_store import ArtifactStore

    s3 = _LaneS3()
    presigned: list[dict] = []
    real = s3.generate_presigned_url

    def _spy(op, Params, ExpiresIn):  # noqa: N803 - boto3 names
        presigned.append({"op": op, **Params})
        return real(op, Params, ExpiresIn)

    s3.generate_presigned_url = _spy  # type: ignore[method-assign]
    monkeypatch.setenv("S3_ENDPOINT", "http://minio.invalid:9000")
    monkeypatch.setattr(ArtifactStore, "_client", lambda self: s3)
    (tmp_path / "sut.py").write_text("x = 1\n")

    sb = KubeJobSandbox("img", repo_pvc=None, pack_workdir=True)
    cmds, _uri, _env, _collect, discard = sb._pack_lane(
        "tfsbx-t", ["pytest -q"], str(tmp_path), 900
    )
    discard()
    assert "timeout -k 10 780 bash -c" in cmds[0]
    assert cmds[0].index("timeout -k 10") < cmds[0].index("TF_LANE_OUT_URL")
    put = next(p for p in presigned if p["op"] == "put_object")
    assert put["Tagging"] == "role=workspace"
    # fake URL has no x-amz-tagging query param, so curl must send the header
    assert "x-amz-tagging: role=workspace" in cmds[0]
    assert s3.objects == {}

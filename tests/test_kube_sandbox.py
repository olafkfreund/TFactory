"""RFC-0005 Tier A: k8s Job manifest builder for the Nix lane (pure, no cluster)."""

from __future__ import annotations

from tools.runners.kube_sandbox import build_job_manifest, pvc_subpath


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
        "j2", "img", ["nix develop /work#default -c playwright test"],
        repo_pvc="tf-workspaces", repo_subpath="workspaces/proj",
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
    assert vols["nix-store"]["persistentVolumeClaim"]["claimName"] == "tfactory-nix-store"
    assert "repo" in vols
    mounts = {vm["name"]: vm for vm in t["containers"][0]["volumeMounts"]}
    assert mounts["nix-store"]["mountPath"] == "/nix"
    init = t["initContainers"][0]
    assert init["name"] == "seed-nix-store"
    assert init["volumeMounts"][0]["mountPath"] == "/warm"
    assert "/warm/store" in init["command"][-1]

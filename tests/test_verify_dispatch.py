#!/usr/bin/env python3
"""Tests for the env-gated verify control/execution split (RFC-0016, TFactory #466).

Covers:
- ``verify_exec_mode`` selects in-pod (default / any other value) vs kubejob
  (``TFACTORY_VERIFY_EXEC=kubejob``).
- ``build_verify_job_manifest`` produces a correct Job: the configured image, the
  ``python -m agents.verify_pipeline`` command (plain, or wrapped in ``nix
  develop`` when ``nix_develop`` is set), the worktree + warm-store mounts, the
  ``tfactory-sandbox`` SA with token automount (it dispatches nested lane Jobs),
  and the JOB_ID / CORRELATION_KEY / FACTORY_SERVICE / PYTHONPATH env.
- ``resolve_verify_image`` prefers TFACTORY_VERIFY_IMAGE, then TFACTORY_IMAGE,
  then the thin nix-runner fallback (the #466 "use the service's own image" fix
  so the orchestration Job can import the ``agents`` backend).
- ``verify_job_name`` is DNS-1123 safe and prefixed ``factory-tfactory-``.
- ``dispatch_verify_job`` returns None (fall back to in-pod) when the sandbox is
  unconfigured, and records a queued row + k8s-job worker_ref when it is; the
  applied Job runs on the verify (runtime) image, plain ``python -m
  agents.verify_pipeline`` (no outer nix develop — the lanes it spawns do that),
  with the backend on PYTHONPATH.
- ``reconcile_verify_job`` / ``is_terminal_record`` mark terminal from the
  durable row (the control plane reconciles by polling Postgres).
- ``reap_if_orphaned`` marks a vanished or deadline-exceeded Job ``stuck`` (#464)
  and leaves an already-terminal / still-running row untouched.

The durable store is the REAL ``DbJobStateStore`` on in-memory async SQLite (no
Postgres / no cluster), injected via the ``store=`` seam.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from agents import verify_dispatch as vd
from agents.verify_dispatch import (
    VerifyJobConfig,
    build_verify_job_manifest,
    is_terminal_record,
    resolve_backend_path,
    resolve_verify_image,
    verify_exec_mode,
    verify_job_name,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.database.models import Base  # noqa: E402
from server.services.job_state_store import (  # noqa: E402
    DbJobStateStore,
    get_job_state_store,
)

_IMAGE = "ghcr.io/olafkfreund/tfactory-runner-nix:latest"
# The TFactory RUNTIME image (ships the `agents` backend) the verify Job uses.
_RUNTIME_IMAGE = "ghcr.io/olafkfreund/tfactory:latest"
_BACKEND_PATH = "/home/projects/MagesticAI/apps/backend"
_WEB_SERVER_PATH = "/home/projects/MagesticAI/apps/web-server"


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield get_job_state_store(s)
    await engine.dispose()


class _FakeSandbox:
    """Stand-in for ``KubeJobSandbox`` — carries the coordinates dispatch reads."""

    def __init__(self, namespace="factory", data_root="/home/nonroot/.tfactory"):
        self.namespace = namespace
        self.data_root = data_root
        self.image = _IMAGE
        self.repo_pvc = "tfactory-data"
        self.nix_store_pvc = "tfactory-nix-store"


class _RecordingApply:
    """Injectable ``apply_fn`` that records the manifest it was asked to create."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def __call__(self, namespace: str, manifest: dict) -> None:
        self.calls.append((namespace, manifest))
        if self.fail:
            raise RuntimeError("simulated k8s apply failure")


# ─── verify_exec_mode (env selects in-pod vs kubejob) ─────────────────────────


def test_verify_exec_mode_defaults_inpod(monkeypatch):
    monkeypatch.delenv("TFACTORY_VERIFY_EXEC", raising=False)
    assert verify_exec_mode() == "inpod"


def test_verify_exec_mode_kubejob_opt_in(monkeypatch):
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "kubejob")
    assert verify_exec_mode() == "kubejob"


def test_verify_exec_mode_unknown_value_is_inpod(monkeypatch):
    # Any value other than the exact "kubejob" keeps the safe default.
    monkeypatch.setenv("TFACTORY_VERIFY_EXEC", "docker")
    assert verify_exec_mode() == "inpod"


# ─── resolve_verify_image / resolve_backend_path (#466 "service's own image") ──


def test_resolve_verify_image_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("TFACTORY_VERIFY_IMAGE", _RUNTIME_IMAGE)
    monkeypatch.setenv("TFACTORY_IMAGE", "ghcr.io/x/other:1")
    # The explicit verify-image override wins, even over the running image.
    assert resolve_verify_image(_IMAGE) == _RUNTIME_IMAGE


def test_resolve_verify_image_uses_running_image_when_no_override(monkeypatch):
    monkeypatch.delenv("TFACTORY_VERIFY_IMAGE", raising=False)
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    # The chart-injected running image is the default verify image (ships agents).
    assert resolve_verify_image(_IMAGE) == _RUNTIME_IMAGE


def test_resolve_verify_image_falls_back_to_nix_runner_when_unset(monkeypatch):
    monkeypatch.delenv("TFACTORY_VERIFY_IMAGE", raising=False)
    monkeypatch.delenv("TFACTORY_IMAGE", raising=False)
    # Last resort (dev/test): the thin nix runner — logged WARNING, still buildable.
    assert resolve_verify_image(_IMAGE) == _IMAGE


def test_resolve_backend_path_default_and_override(monkeypatch):
    monkeypatch.delenv("APP_BACKEND_PATH", raising=False)
    assert resolve_backend_path() == _BACKEND_PATH
    monkeypatch.setenv("APP_BACKEND_PATH", "/opt/tf/backend")
    assert resolve_backend_path() == "/opt/tf/backend"


# ─── verify_job_name (DNS-1123 safe, prefixed) ────────────────────────────────


def test_verify_job_name_prefix_and_dns_safe():
    name = verify_job_name("proj-abc:042-verify")
    assert name.startswith("factory-tfactory-")
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9-]+", name) is not None


def test_verify_job_name_sanitizes_and_truncates():
    name = verify_job_name("A_VERY/LONG::Job__Id::With::Junk::1234567890")
    assert name.startswith("factory-tfactory-")
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9-]+", name) is not None


# ─── build_verify_job_manifest (Job manifest correctness) ─────────────────────


def _cfg(**kw) -> VerifyJobConfig:
    base = {
        "job_id": "proj-abc:042-verify",
        # The verify Job runs on the RUNTIME image (ships `agents`), not the thin
        # nix runner — that was the #466 ModuleNotFoundError bug.
        "image": _RUNTIME_IMAGE,
        "spec_subpath": "workspaces/proj/.tfactory/specs/042",
        "project_subpath": "workspaces/proj",
        "repo_pvc": "tfactory-data",
        "nix_store_pvc": "tfactory-nix-store",
        "correlation_key": 482,
        "backend_path": _BACKEND_PATH,
    }
    base.update(kw)
    return VerifyJobConfig(**base)  # type: ignore[arg-type]


def test_seed_creds_off_by_default(monkeypatch):
    # No TFACTORY_VERIFY_CLI_CREDS_SECRET → env-auth-only path (#480) unchanged:
    # no seed initContainer, no cc-* / cli-creds volumes.
    monkeypatch.delenv("TFACTORY_VERIFY_CLI_CREDS_SECRET", raising=False)
    pod = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    # The base manifest has a seed-nix-store initContainer; we must NOT add a
    # seed-creds one (nor any cli-creds / cc-* volumes) when off.
    init_names = {c["name"] for c in pod.get("initContainers", [])}
    assert "seed-creds" not in init_names
    vol_names = {v["name"] for v in pod.get("volumes", [])}
    assert "cli-creds" not in vol_names
    assert "cc-claude" not in vol_names


def test_seed_creds_injected_when_secret_configured(monkeypatch):
    # #481: opt-in file-auth credential seeding into the verify Job pod.
    monkeypatch.setenv("TFACTORY_VERIFY_CLI_CREDS_SECRET", "factory-cli-creds")
    pod = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]

    init = next(c for c in pod["initContainers"] if c["name"] == "seed-creds")
    assert init["image"].startswith("busybox")
    seed_mount = next(mt for mt in init["volumeMounts"] if mt["name"] == "cli-creds")
    assert seed_mount["mountPath"] == "/seed"
    assert seed_mount.get("readOnly") is True
    script = init["args"][0]
    for key in (
        "claude-credentials.json",
        "codex-auth.json",
        "copilot-apps.json",
        "gemini-oauth_creds.json",
    ):
        assert f"/seed/{key}" in script
    assert "|| true" in script  # tolerant: a missing provider file never aborts

    cli = next(v for v in pod["volumes"] if v["name"] == "cli-creds")
    assert cli["secret"]["secretName"] == "factory-cli-creds"

    mounts = {mt["mountPath"] for mt in pod["containers"][0]["volumeMounts"]}
    assert {
        "/home/nonroot/.claude",
        "/home/nonroot/.codex",
        "/home/nonroot/.gemini",
        "/home/nonroot/.config",
    } <= mounts


def test_seed_creds_init_container_is_hardened(monkeypatch):
    # #651: the seed-creds initContainer carries the same hardened
    # securityContext as the lane/seed containers (no escalation, drop ALL +
    # co-mount add-backs).
    monkeypatch.setenv("TFACTORY_VERIFY_CLI_CREDS_SECRET", "factory-cli-creds")
    pod = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    init = next(c for c in pod["initContainers"] if c["name"] == "seed-creds")
    sc = init["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]


def test_verify_job_pod_pins_seccomp_runtime_default():
    # #651: the verify-orchestration Job inherits the shared builder's pod
    # hardening (seccomp RuntimeDefault) and its container the restricted
    # securityContext.
    pod = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    sc = pod["containers"][0]["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]


def test_manifest_is_a_job_with_the_configured_image():
    m = build_verify_job_manifest(_cfg())
    assert m["kind"] == "Job"
    assert m["spec"]["backoffLimit"] == 0  # no silent retries
    c = m["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == _RUNTIME_IMAGE


def test_manifest_runs_verify_pipeline_directly_by_default():
    # Default (nix_develop=False) — the orchestration Job runs the pipeline plainly
    # on the runtime image; the lanes it spawns nix-develop, not this Job (#466).
    m = build_verify_job_manifest(_cfg(nix_develop=False))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop" not in cmd
    assert "python -m agents.verify_pipeline" in cmd
    assert "--spec /work/workspaces/proj/.tfactory/specs/042" in cmd
    assert "--project /work/workspaces/proj" in cmd
    assert "--job-id proj-abc:042-verify" in cmd
    assert "--correlation-key 482" in cmd


def test_manifest_can_still_wrap_in_nix_develop():
    # The builder retains the nix-develop capability for callers that want the
    # toolchain from a per-task flake; it targets the PROJECT worktree.
    m = build_verify_job_manifest(_cfg(nix_develop=True))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop path:/work/workspaces/proj#default" in cmd
    assert "python -m agents.verify_pipeline" in cmd


def test_manifest_nix_develop_targets_explicit_flake_subpath():
    # When the flake lives in a different dir than the project, develop that dir.
    m = build_verify_job_manifest(_cfg(flake_subpath="workspaces/proj/env"))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop path:/work/workspaces/proj/env#default" in cmd


def test_manifest_without_nix_develop_runs_verify_directly():
    # A non-nix task (no flake) must run the verify directly on the image, not
    # nix develop a nonexistent flake.
    m = build_verify_job_manifest(_cfg(nix_develop=False))
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop" not in cmd
    assert "python -m agents.verify_pipeline" in cmd


def test_manifest_propagates_nix_sandbox_env_when_set(monkeypatch):
    # The verify pipeline running inside the Job dispatches the nested Nix lane
    # Job via nix_runner_from_env(), which reads these off the env. They live on
    # the Deployment but aren't inherited by the dispatched Job, so the manifest
    # must forward them — else the Nix lane silently falls back to host.
    monkeypatch.setenv(
        "TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/tfactory-runner-nix:latest"
    )
    monkeypatch.setenv("TFACTORY_WORKSPACES_PVC", "tfactory-data")
    monkeypatch.setenv("TFACTORY_NIX_STORE_PVC", "tfactory-nix-store")
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    env = {e["name"]: e["value"] for e in ps["containers"][0]["env"]}
    assert env["TFACTORY_NIX_RUNNER_IMAGE"] == "ghcr.io/x/tfactory-runner-nix:latest"
    assert env["TFACTORY_WORKSPACES_PVC"] == "tfactory-data"
    assert env["TFACTORY_NIX_STORE_PVC"] == "tfactory-nix-store"


def test_nix_in_image_flag_reaches_the_dispatched_job(monkeypatch):
    """#623: the flag MUST travel with TFACTORY_NIX_STORE_PVC.

    nix_runner_from_env() runs again *inside* the dispatched Job. If the flag
    does not reach it, it reads False there and re-mounts the very RWO PVC the
    flag exists to drop — the flip lands on the control plane and silently misses
    the nested Job. That half-applied shape is exactly how AIFactory#840 broke
    its gate lane.
    """
    monkeypatch.setenv(
        "TFACTORY_NIX_RUNNER_IMAGE", "ghcr.io/x/tfactory-runner-nix:latest"
    )
    monkeypatch.setenv("TFACTORY_NIX_STORE_PVC", "tfactory-nix-store")
    monkeypatch.setenv("TFACTORY_NIX_IN_IMAGE", "true")
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    env = {e["name"]: e["value"] for e in ps["containers"][0]["env"]}
    assert env.get("TFACTORY_NIX_IN_IMAGE") == "true", env


def test_manifest_omits_nix_sandbox_env_when_unset(monkeypatch):
    for v in (
        "TFACTORY_NIX_RUNNER_IMAGE",
        "TFACTORY_WORKSPACES_PVC",
        "TFACTORY_NIX_STORE_PVC",
        "TFACTORY_NIX_IN_IMAGE",
        "TFACTORY_SANDBOX_NAMESPACE",
    ):
        monkeypatch.delenv(v, raising=False)
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    names = {e["name"] for e in ps["containers"][0]["env"]}
    assert "TFACTORY_NIX_RUNNER_IMAGE" not in names


def test_triager_side_effect_flags_reach_the_dispatched_job(monkeypatch):
    # #719: the triager runs INSIDE this verify Job; the side-effect flags live
    # only on the control-plane Deployment, so they must be forwarded or the
    # verdict is computed but never posted (git_writer / pr_comment dry-run).
    monkeypatch.setenv("TFACTORY_TRIAGER_GIT_WRITE", "1")
    monkeypatch.setenv("TFACTORY_TRIAGER_PR_COMMENT", "1")
    monkeypatch.setenv("TFACTORY_PR_STATUS", "1")
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    env = {e["name"]: e["value"] for e in ps["containers"][0]["env"]}
    assert env.get("TFACTORY_TRIAGER_GIT_WRITE") == "1", env
    assert env.get("TFACTORY_TRIAGER_PR_COMMENT") == "1", env
    assert env.get("TFACTORY_PR_STATUS") == "1", env


def test_triager_flags_omitted_when_unset(monkeypatch):
    for v in (
        "TFACTORY_TRIAGER_GIT_WRITE",
        "TFACTORY_TRIAGER_PR_COMMENT",
        "TFACTORY_PR_STATUS",
    ):
        monkeypatch.delenv(v, raising=False)
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    names = {e["name"] for e in ps["containers"][0]["env"]}
    assert "TFACTORY_TRIAGER_GIT_WRITE" not in names
    assert "TFACTORY_TRIAGER_PR_COMMENT" not in names


def test_manifest_uses_tfactory_sandbox_sa_with_token_automount():
    # The verify Job dispatches nested per-lane Jobs (Nix pytest/browser lanes via
    # KubeJobSandbox.create_namespaced_job), so it needs the SA token mounted to
    # authenticate to the k8s API — otherwise the nested dispatch fails and the
    # lane silently falls back to the host runner.
    ps = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]
    assert ps["serviceAccountName"] == "tfactory-sandbox"
    assert ps["automountServiceAccountToken"] is True


def test_manifest_mounts_worktree_and_warm_nix_store():
    m = build_verify_job_manifest(_cfg())
    ps = m["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in ps["volumes"]}
    assert vols["repo"]["persistentVolumeClaim"]["claimName"] == "tfactory-data"
    assert (
        vols["nix-store"]["persistentVolumeClaim"]["claimName"] == "tfactory-nix-store"
    )
    mounts = {vm["name"]: vm for vm in ps["containers"][0]["volumeMounts"]}
    assert mounts["repo"]["mountPath"] == "/work"
    assert mounts["nix-store"]["mountPath"] == "/nix"


def test_manifest_carries_job_state_env():
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["JOB_ID"] == "proj-abc:042-verify"
    assert env["FACTORY_SERVICE"] == "tfactory"
    assert env["CORRELATION_KEY"] == "482"


def test_manifest_sets_pythonpath_so_agents_imports():
    # The #466 fix: without the backend on PYTHONPATH the verify Job died
    # `ModuleNotFoundError: No module named 'agents'`. Both the backend (agents)
    # and the web-server sibling (server.* for the terminal store write) are set.
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    entries = env["PYTHONPATH"].split(":")
    assert _BACKEND_PATH in entries
    assert _WEB_SERVER_PATH in entries
    # The image leaves PYTHONPATH unset, so there must be NO literal k8s self-ref
    # ($(PYTHONPATH)) that k8s would fail to expand and leave as poison text.
    assert "$(PYTHONPATH)" not in env["PYTHONPATH"]


def test_manifest_omits_pythonpath_when_backend_path_blank():
    c = build_verify_job_manifest(_cfg(backend_path=""))["spec"]["template"]["spec"][
        "containers"
    ][0]
    assert all(e["name"] != "PYTHONPATH" for e in c["env"])


def test_manifest_passes_database_url_through_when_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://h/db")
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DATABASE_URL"] == "postgresql+asyncpg://h/db"


def test_manifest_omits_database_url_when_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    c = build_verify_job_manifest(_cfg())["spec"]["template"]["spec"]["containers"][0]
    assert all(e["name"] != "DATABASE_URL" for e in c["env"])


def test_manifest_labels_durable_coordinates():
    labels = build_verify_job_manifest(_cfg())["metadata"]["labels"]
    assert labels["factory.io/kind"] == "verify"
    assert "factory.io/job-id" in labels


# ─── dispatch_verify_job (fall back vs record) ────────────────────────────────


# ─── #466 env round-4: the verify Job carries the LLM credential (env, not argv) ─
#
# The verify pipeline's evaluator calls create_client → require_auth_token; the
# round-3 Job env lacked the OAuth token, so it died ``ValueError: No OAuth token
# found`` (SAME class as the AIFactory build Job). The fix injects
# CLAUDE_CODE_OAUTH_TOKEN into the container env — secretKeyRef when configured
# (no literal in the manifest), else a resolved value — NEVER argv (cf. #477).


def _clear_oauth_env(monkeypatch):
    for var in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_AUTH_TOKEN",
        "TFACTORY_VERIFY_OAUTH_SECRET_NAME",
        "TFACTORY_VERIFY_OAUTH_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _env_of(manifest_or_cfg):
    c = manifest_or_cfg["spec"]["template"]["spec"]["containers"][0]
    return c["env"]


def test_manifest_injects_oauth_via_secret_ref(monkeypatch):
    # Preferred path: a configured Secret name → secretKeyRef, NO literal token.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TFACTORY_VERIFY_OAUTH_SECRET_NAME", "tfactory-claude-oauth")
    env = _env_of(build_verify_job_manifest(_cfg()))
    tok = next(e for e in env if e["name"] == "CLAUDE_CODE_OAUTH_TOKEN")
    assert "value" not in tok  # no literal token in the manifest
    ref = tok["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "tfactory-claude-oauth"
    assert ref["key"] == "oauth-token"  # default key


def test_manifest_secret_ref_key_overridable(monkeypatch):
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TFACTORY_VERIFY_OAUTH_SECRET_NAME", "s")
    monkeypatch.setenv("TFACTORY_VERIFY_OAUTH_SECRET_KEY", "claude-token")
    env = _env_of(build_verify_job_manifest(_cfg()))
    ref = next(e for e in env if e["name"] == "CLAUDE_CODE_OAUTH_TOKEN")["valueFrom"]
    assert ref["secretKeyRef"]["key"] == "claude-token"


def test_manifest_injects_resolved_oauth_value_when_no_secret(monkeypatch):
    # Fallback: no Secret configured → resolve via core.auth and set as an env
    # VALUE (still env, never argv). The token comes from the pod env here.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-deadbeef")
    env = _env_of(build_verify_job_manifest(_cfg()))
    tok = next(e for e in env if e["name"] == "CLAUDE_CODE_OAUTH_TOKEN")
    assert tok["value"] == "sk-ant-oat01-deadbeef"
    assert "valueFrom" not in tok


def test_manifest_omits_oauth_when_unresolvable(monkeypatch):
    # No Secret, no resolvable token → the entry is omitted (the Job fails closed
    # inside with 'No OAuth token found' rather than getting a blank/poison value).
    _clear_oauth_env(monkeypatch)
    monkeypatch.setattr(vd, "_resolve_oauth_token", lambda: None)
    env = _env_of(build_verify_job_manifest(_cfg()))
    assert all(e["name"] != "CLAUDE_CODE_OAUTH_TOKEN" for e in env)


def test_oauth_token_never_appears_in_argv(monkeypatch):
    # SECURITY: the resolved token must be in the env ONLY, never on the command
    # line (cf. the separate PAT-in-argv leak #477).
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-secret-xyz")
    m = build_verify_job_manifest(_cfg())
    cmd = m["spec"]["template"]["spec"]["containers"][0]["command"]
    assert all("sk-ant-oat01-secret-xyz" not in part for part in cmd)


def test_secret_ref_token_value_never_in_manifest(monkeypatch):
    # With a secretKeyRef the literal token is nowhere in the rendered manifest.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TFACTORY_VERIFY_OAUTH_SECRET_NAME", "s")
    # Even if a token is ALSO resolvable, the secretKeyRef path wins → no literal.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-should-not-leak")
    import json as _json

    rendered = _json.dumps(build_verify_job_manifest(_cfg()))
    assert "sk-ant-oat01-should-not-leak" not in rendered


def test_manifest_passes_through_sdk_provider_env(monkeypatch):
    # Custom endpoint + model overrides forwarded so the Job resolves as in-pod.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")
    env = {
        e["name"]: e.get("value") for e in _env_of(build_verify_job_manifest(_cfg()))
    }
    assert env["ANTHROPIC_BASE_URL"] == "https://proxy.example/v1"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-8"


def test_manifest_forwards_non_claude_provider_env(monkeypatch):
    # A verify routed to a NON-Claude model needs that provider's env too — the
    # evaluator routes by model (openai/gemini/ollama/github). Claude-only
    # injection would break any non-Claude verify. Forward the full set.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-123")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://ollama.example/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "ock-456")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-789")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-789")
    monkeypatch.setenv("OLLAMA_API_KEY", "oll-000")
    monkeypatch.setenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.cloud")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_zzz")
    monkeypatch.setenv("GITHUB_MODELS_DEFAULT", "openai/gpt-4.1")
    monkeypatch.setenv("QA_LLM_PROVIDER", "openai")
    env = {
        e["name"]: e.get("value") for e in _env_of(build_verify_job_manifest(_cfg()))
    }
    assert env["OPENAI_API_KEY"] == "sk-openai-123"
    assert env["OPENAI_COMPATIBLE_BASE_URL"] == "https://ollama.example/v1"
    assert env["OPENAI_COMPATIBLE_API_KEY"] == "ock-456"
    assert env["GEMINI_API_KEY"] == "gem-789"
    assert env["GOOGLE_API_KEY"] == "goog-789"
    assert env["OLLAMA_API_KEY"] == "oll-000"
    assert env["OLLAMA_CLOUD_BASE_URL"] == "https://ollama.cloud"
    assert env["GITHUB_TOKEN"] == "ghp_zzz"
    assert env["GITHUB_MODELS_DEFAULT"] == "openai/gpt-4.1"
    assert env["QA_LLM_PROVIDER"] == "openai"


def test_manifest_provider_secrets_via_secret_ref(monkeypatch):
    # With an env-Secret configured, provider SECRETS (keys/tokens) source via
    # secretKeyRef (no literal in the manifest); non-secret config stays a value.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv(
        "TFACTORY_VERIFY_PROVIDER_SECRET_NAME", "tfactory-provider-creds"
    )
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://ollama.example/v1")
    env = {e["name"]: e for e in _env_of(build_verify_job_manifest(_cfg()))}
    oai = env["OPENAI_API_KEY"]  # secret → ref even though not set on the pod
    ref = oai["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "tfactory-provider-creds"
    assert ref["key"] == "openai-api-key"
    assert ref["optional"] is True
    assert "value" not in oai
    # non-secret config still forwarded as a plain value
    assert env["OPENAI_COMPATIBLE_BASE_URL"]["value"] == "https://ollama.example/v1"


def test_provider_secret_value_never_in_manifest(monkeypatch):
    # SECURITY: with the env-Secret path the literal provider key is nowhere in
    # the rendered manifest, even if also set on the pod env.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TFACTORY_VERIFY_PROVIDER_SECRET_NAME", "s")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-should-not-leak")
    import json as _json

    rendered = _json.dumps(build_verify_job_manifest(_cfg()))
    assert "sk-openai-should-not-leak" not in rendered


def test_manifest_excludes_anthropic_api_key(monkeypatch):
    # ANTHROPIC_API_KEY is never forwarded (auth.py never falls back to it —
    # forwarding would risk silent API billing).
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-should-not-forward")
    env = _env_of(build_verify_job_manifest(_cfg()))
    assert all(e["name"] != "ANTHROPIC_API_KEY" for e in env)


def test_manifest_omits_sdk_env_when_unset(monkeypatch):
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-x")
    for var in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    env_names = {e["name"] for e in _env_of(build_verify_job_manifest(_cfg()))}
    assert "ANTHROPIC_BASE_URL" not in env_names
    assert "ANTHROPIC_MODEL" not in env_names


async def test_dispatch_propagates_oauth_env_to_applied_job(monkeypatch, store):
    # End-to-end: the applied verify Job manifest carries the OAuth credential the
    # evaluator needs — the round-4 fix for the 'No OAuth token found' Failed Job.
    _clear_oauth_env(monkeypatch)
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    monkeypatch.setenv("TFACTORY_VERIFY_OAUTH_SECRET_NAME", "tfactory-claude-oauth")
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:046",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    _, manifest = apply.calls[0]
    env = _env_of(manifest)
    tok = next(e for e in env if e["name"] == "CLAUDE_CODE_OAUTH_TOKEN")
    assert tok["valueFrom"]["secretKeyRef"]["name"] == "tfactory-claude-oauth"


async def test_dispatch_falls_back_when_sandbox_unconfigured(monkeypatch, store):
    # No TFACTORY_NIX_RUNNER_IMAGE → nix_runner_from_env() is None → None.
    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    result = await vd.dispatch_verify_job(
        job_id="j1",
        spec_dir=Path("/home/nonroot/.tfactory/ws/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws"),
        store=store,
    )
    assert result is None
    # No row was created (we never got far enough to record).
    assert await store.get("j1") is None


async def test_dispatch_records_queued_row_with_k8s_worker_ref(store):
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042-verify",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        correlation_key=99,
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    assert result.job_name == verify_job_name("proj:042-verify")
    assert result.job_name.startswith("factory-tfactory-")
    assert result.worker_ref["kind"] == "k8s-job"
    assert result.worker_ref["job_name"] == result.job_name

    rec = await store.get("proj:042-verify")
    assert rec is not None
    assert rec["lifecycle_state"] == "queued"
    assert rec["worker_ref"]["kind"] == "k8s-job"
    assert rec["correlation_key"] == "99"


async def test_dispatch_applies_the_verify_job_manifest(monkeypatch, store):
    # The dispatch must actually create the Job (#466: it never did before the
    # wiring fix). The applied manifest is the verify-orchestration Job, on the
    # RUNTIME image (not the thin nix runner), running the pipeline directly with
    # the backend on PYTHONPATH — the #466 ModuleNotFoundError fix.
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    monkeypatch.delenv("APP_BACKEND_PATH", raising=False)
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    assert len(apply.calls) == 1
    ns, manifest = apply.calls[0]
    assert ns == "factory"
    assert manifest["kind"] == "Job"
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    # The orchestration Job runs the TFactory RUNTIME image, NOT the nix runner.
    assert container["image"] == _RUNTIME_IMAGE
    cmd = container["command"][2]
    assert "python -m agents.verify_pipeline" in cmd
    # No outer nix develop: the orchestration runs the pipeline directly; the lanes
    # it spawns nix-develop. (Without this the Job tried to nix the SUT toolchain.)
    assert "nix develop" not in cmd
    # Backend on PYTHONPATH so `agents` imports (the #466 ModuleNotFoundError fix).
    env = {e["name"]: e["value"] for e in container["env"]}
    assert _BACKEND_PATH in env["PYTHONPATH"].split(":")


async def test_dispatch_falls_back_to_inpod_when_apply_fails(store):
    # A cluster/apply gap must NOT strand the verify: dispatch returns None so the
    # caller runs the in-pod path instead.
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply(fail=True)
    result = await vd.dispatch_verify_job(
        job_id="proj:043",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is None  # → caller falls back to in-pod


# ─── reconcile_verify_job + is_terminal_record ────────────────────────────────


async def test_reconcile_marks_terminal_from_job_state(store):
    await store.enqueue("jt")
    await store.grant_slot("jt")
    # The Job wrote its terminal verdict row (done).
    await store.update_status("jt", service_status="triaged", has_verdict=True)

    rec = await vd.reconcile_verify_job("jt", store=store)
    assert rec is not None
    assert rec["lifecycle_state"] == "done"
    assert is_terminal_record(rec) is True


async def test_reconcile_running_is_not_terminal(store):
    await store.enqueue("jr")
    await store.grant_slot("jr")
    rec = await vd.reconcile_verify_job("jr", store=store)
    assert rec["lifecycle_state"] == "running"
    assert is_terminal_record(rec) is False


def test_is_terminal_record_handles_none():
    assert is_terminal_record(None) is False


# ─── reap_if_orphaned (#464) ──────────────────────────────────────────────────


async def test_reap_marks_vanished_job_stuck(store):
    await store.enqueue("jv")
    await store.grant_slot("jv")  # running, no terminal write
    rec = await vd.reap_if_orphaned(
        "jv", job_exists=False, job_active=False, store=store
    )
    assert rec is not None
    assert rec["lifecycle_state"] == "stuck"
    assert rec["error"]  # never-overclaim: a reaped job carries a reason
    assert "vanished" in rec["error"]


async def test_reap_marks_deadline_exceeded_no_verdict_stuck(store):
    await store.enqueue("jd")
    await store.grant_slot("jd")
    # Job object still present but finished (deadline/backoff) with no verdict.
    rec = await vd.reap_if_orphaned(
        "jd", job_exists=True, job_active=False, store=store
    )
    assert rec is not None
    assert rec["lifecycle_state"] == "stuck"
    assert "no verdict" in rec["error"]


async def test_reap_leaves_terminal_row_untouched(store):
    await store.enqueue("jdone")
    await store.grant_slot("jdone")
    await store.update_status("jdone", service_status="triaged", has_verdict=True)
    rec = await vd.reap_if_orphaned(
        "jdone", job_exists=False, job_active=False, store=store
    )
    assert rec is None  # idempotent — the Job's own terminal write wins
    assert (await store.get("jdone"))["lifecycle_state"] == "done"


async def test_reap_leaves_running_job_untouched(store):
    await store.enqueue("jrun")
    await store.grant_slot("jrun")
    rec = await vd.reap_if_orphaned(
        "jrun", job_exists=True, job_active=True, store=store
    )
    assert rec is None  # still running — nothing to reap
    assert (await store.get("jrun"))["lifecycle_state"] == "running"


async def test_reap_no_row_is_noop(store):
    assert (
        await vd.reap_if_orphaned(
            "ghost", job_exists=False, job_active=False, store=store
        )
        is None
    )


# ─── control-plane reconcile + reap tick (the wired loop's body) ──────────────


async def _probe(_results):
    async def _fn(namespace, job_name):
        return _results.get(job_name, (True, True))

    return _fn


async def _dispatch(store, job_id, correlation_key=None):
    return await vd.dispatch_verify_job(
        job_id=job_id,
        spec_dir=Path(f"/home/nonroot/.tfactory/ws/{job_id}/spec"),
        project_dir=Path(f"/home/nonroot/.tfactory/ws/{job_id}"),
        correlation_key=correlation_key,
        sandbox=_FakeSandbox(),
        store=store,
        apply_fn=_RecordingApply(),
    )


async def test_reconcile_tick_reaps_vanished_dispatched_job(store):
    d = await _dispatch(store, "proj:100")
    assert d is not None
    job_name = d.job_name
    probe_fn = await _probe({job_name: (False, False)})  # Job gone, row still active

    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 1
    rec = await store.get("proj:100")
    assert rec["lifecycle_state"] == "stuck"
    assert "vanished" in (rec["error"] or "")


async def test_reconcile_tick_leaves_running_job(store):
    d = await _dispatch(store, "proj:101")
    probe_fn = await _probe({d.job_name: (True, True)})  # still active
    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 0
    assert (await store.get("proj:101"))["lifecycle_state"] == "queued"


async def test_reconcile_tick_skips_terminal_row(store):
    await _dispatch(store, "proj:102")
    # The Job wrote its terminal verdict row (done) — the tick must not touch it.
    await store.update_status("proj:102", service_status="triaged", has_verdict=True)
    probe_fn = await _probe({})  # default (exists, active) — irrelevant once terminal
    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=probe_fn)
    assert reaped == 0
    assert (await store.get("proj:102"))["lifecycle_state"] == "done"


async def test_reconcile_tick_ignores_non_k8s_rows(store):
    # An in-pod verify row (no k8s-job worker_ref) is not the loop's concern.
    await store.enqueue("inpod-1")
    await store.grant_slot("inpod-1")
    reaped = await vd.reconcile_and_reap_once(store=store, probe_fn=await _probe({}))
    assert reaped == 0
    assert (await store.get("inpod-1"))["lifecycle_state"] == "running"


# ─── #466: the verify Job runs on the runtime image, not the thin nix runner ───
#
# The orchestration Job runs `python -m agents.verify_pipeline` — the TFactory
# backend — so it MUST land on an image that ships `agents`. Running it on the
# thin nix runner died `ModuleNotFoundError: No module named 'agents'`. It does
# NOT nix-develop (the lanes it spawns do); the SUT toolchain is a per-lane Job
# concern, not an orchestration concern. (mirrors AIFactory #686.)


async def test_dispatch_uses_runtime_image_not_nix_runner(monkeypatch, store):
    # The applied verify Job runs on the TFACTORY_IMAGE (runtime, ships agents),
    # NOT the sandbox's thin nix-runner image — the #466 ModuleNotFoundError fix.
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    sandbox = _FakeSandbox(namespace="factory")  # sandbox.image is the nix runner
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:042",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    _, manifest = apply.calls[0]
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == _RUNTIME_IMAGE
    assert container["image"] != sandbox.image  # not the thin nix runner


async def test_dispatch_verify_image_override_is_honored(monkeypatch, store):
    # An explicit TFACTORY_VERIFY_IMAGE override wins for the verify Job.
    override = "ghcr.io/x/tfactory-verify:pinned"
    monkeypatch.setenv("TFACTORY_VERIFY_IMAGE", override)
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:044",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    _, manifest = apply.calls[0]
    assert manifest["spec"]["template"]["spec"]["containers"][0]["image"] == override


async def test_dispatch_runs_pipeline_directly_no_nix_develop(monkeypatch, store):
    # The orchestration Job runs the pipeline directly (no outer nix develop) — the
    # lanes it spawns nix-develop, not this Job. True even for a nix task: the
    # orchestration only imports + runs the backend; it never touches the SUT
    # toolchain itself.
    monkeypatch.setenv("TFACTORY_IMAGE", _RUNTIME_IMAGE)
    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()
    result = await vd.dispatch_verify_job(
        job_id="proj:045",
        spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
        project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
        sandbox=sandbox,
        store=store,
        apply_fn=apply,
    )
    assert result is not None
    _, manifest = apply.calls[0]
    cmd = manifest["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "nix develop" not in cmd
    assert "python -m agents.verify_pipeline" in cmd


# ─── BUG 1: the dispatch record succeeds across a private loop (no cross-loop) ──


def test_record_dispatch_succeeds_on_a_foreign_loop(tmp_path, monkeypatch):
    """The blocking dispatch path runs on its OWN loop (``asyncio.run`` in a worker
    thread); the durable store write must still succeed (BUG 1: asyncpg "Future
    attached to a different loop" when a main-loop-pinned pooled connection is
    reused from another loop). The fix makes ``_store_for`` open a fresh engine on
    the using loop. We drive the REAL ``DbJobStateStore`` that ``_store_for`` opens
    against a file-backed SQLite DB, across two independent ``asyncio.run`` loops —
    the production call shape — and assert the dispatch records a durable row that a
    later reconcile (on yet another loop) reads and marks terminal.

    (SQLite opens a connection per loop so it cannot reproduce asyncpg's exact
    loop-pinning failure; this test pins the *correct* behaviour the fresh-engine
    fix guarantees — the store write + reconcile succeed across loop boundaries —
    which is what stranded the verify before the fix.)
    """
    import asyncio

    db_path = tmp_path / "loop.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    # Create the schema once (on a throwaway loop) so the store has a table.
    async def _make_schema() -> None:
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await eng.dispose()

    asyncio.run(_make_schema())

    sandbox = _FakeSandbox(namespace="factory")
    apply = _RecordingApply()

    # Run the dispatch on a fresh loop — _store_for must open its engine on THIS
    # loop (not reuse a main-loop-pinned one), so the write doesn't raise.
    async def _go() -> object:
        return await vd.dispatch_verify_job(
            job_id="loop:1",
            spec_dir=Path("/home/nonroot/.tfactory/ws/proj/spec"),
            project_dir=Path("/home/nonroot/.tfactory/ws/proj"),
            sandbox=sandbox,
            store=None,  # exercise the real _store_for fresh-engine path
            apply_fn=apply,
        )

    result = asyncio.run(_go())
    assert result is not None  # dispatch recorded + applied without a loop error

    # The durable row landed; reconcile (also on its own loop) can read it and a
    # terminal write marks it done — the reaper/reconciler can now produce a verdict.
    async def _verdict() -> str:
        rec = await vd.reconcile_verify_job("loop:1")
        assert rec is not None
        assert rec["lifecycle_state"] == "queued"
        assert rec["worker_ref"]["kind"] == "k8s-job"
        # The Job writes its terminal row; emulate that write, then reconcile.
        async with vd._store_for(None) as (s, _owned):
            await s.update_status("loop:1", service_status="triaged", has_verdict=True)
        done = await vd.reconcile_verify_job("loop:1")
        return done["lifecycle_state"]

    assert asyncio.run(_verdict()) == "done"


def test_verify_job_sets_data_root_env():
    """The verify Job declares TFACTORY_DATA_ROOT = its mount, so the nested Nix
    sandbox resolves co-mount subPaths against the right PVC root — without it
    pvc_subpath returns None, the Nix Job mounts nothing, and every AC rejects on
    an empty /work (#623)."""
    m = build_verify_job_manifest(_cfg())
    env = m["spec"]["template"]["spec"]["containers"][0]["env"]
    dr = next((e for e in env if e["name"] == "TFACTORY_DATA_ROOT"), None)
    assert dr is not None and dr["value"] == "/work"  # VerifyJobConfig.mount default

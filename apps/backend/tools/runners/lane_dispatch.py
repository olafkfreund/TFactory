"""Lane → runner dispatcher — Task 4 (#5), restructured in v0.2 Task 0 (#16).

Given a Subtask's ``lane``, route to the right execution path. The v0.2
spine reorganises lanes around modality (browser-first), not security
category — see docs/plans/2026-05-28-enterprise-test-frameworks-design.md
Decision 2.

  v0.2 lit lanes (real runner present):
    unit        → DockerRunner (per-framework container: pytest / Jest / JUnit / …)
    browser     → DockerRunner + AppRuntime (Playwright/Cypress in container; app via docker-compose)
    api         → DockerRunner (httpx/supertest/REST Assured in container)
    integration → DockerRunner + AppRuntime (TestContainers etc.)
    mutation    → DockerRunner (per-framework: mutmut/Stryker/PIT)

  v0.1 alias lanes (deprecated through v0.2, removed in v0.3):
    functional  → collapse to unit (most v0.1 usage was unit tests)
    sast/dast/fuzz → out of scope per Decision 2; map to unit + warn

The dispatcher is intentionally thin — it owns lane→runner mapping and
nothing else. Test-plan iteration / fan-out / aggregation is the
Executor's (Task 8) job.

Browser + Integration lane dispatch (Task 8 / #24):
  ``dispatch_browser_lane()`` wraps the DockerRunner invocation in an
  ``AppRuntime`` lifecycle so the target app is running when Playwright
  tests execute.  It injects ``TFACTORY_TARGET_URL`` into the test
  container via ``extra_env`` so the Playwright spec can read it without
  hardcoding a port.  Callers that do NOT need the AppRuntime lifecycle
  (unit / api / mutation) continue to use the plain ``dispatch_lane()``
  path unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .deploy_runner import run_deploy_lane
from .docker_runner import DockerRunner, DockerRunResult

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LaneNotImplementedError(NotImplementedError):
    """Raised when the requested lane has no executor in this MVP."""


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

# Lanes that have a real runner. v0.1 had only "functional"; v0.2 lights
# the full modality spine. Single source of truth so tests can iterate.
_MVP_LIT_LANES: frozenset[str] = frozenset(
    {
        "unit",  # v0.2 (Task 7 — Jest / pytest images)
        "browser",  # v0.2 (Task 7 + Task 8 — Playwright image + AppRuntime)
        "api",  # v0.2 (Task 7 — same runner images as unit)
        "integration",  # v0.2 (Task 7 + Task 8 — runner + AppRuntime)
        "mutation",  # v0.2 (Task 7 — Stryker / mutmut images)
    }
)

# Deprecated v0.1 names — see test_plan.enums._V01_LANE_ALIASES. Listed
# here so the dispatcher can route legacy plans (still parsing through
# v0.2) cleanly. Removed in v0.3.
_DEPRECATED_V01_ALIASES: dict[str, str] = {
    "functional": "unit",
    "sast": "unit",  # out of scope per Decision 2
    "dast": "unit",  # out of scope
    "fuzz": "unit",  # out of scope
}

# Out-of-scope lane keys that should NEVER be in v0.2 plans. If they appear,
# emit a structured error rather than a generic NotImplementedError so the
# Planner can detect + replan.
_LANE_PHASES: dict[str, str] = {
    "sast": "out of scope (Decision 2 — use a separate security pipeline)",
    "deps": "out of scope (security pipeline)",
    "secrets": "out of scope (security pipeline)",
    "dast": "out of scope (security pipeline)",
    "fuzz": "out of scope (Decision 2 — property-based testing folded into 'unit' lane)",
}


@dataclass
class DispatchResult:
    """What the dispatcher hands back to the Executor (Task 8)."""

    lane: str
    runner_used: str  # "docker" | "native" | "stub" | "deploy"
    docker_result: DockerRunResult | None = None
    deploy_result: Any | None = None  # DeployLaneResult for the RFC-0013 deploy lane
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def dispatch_lane(
    *,
    lane: str,
    docker_runner: DockerRunner | None = None,
    docker_run_kwargs: dict[str, Any] | None = None,
) -> DispatchResult:
    """Route a lane to its runner.

    Args:
        lane: One of the strings from ``test_plan.enums.Lane`` (snake_case
            value, e.g. ``"functional"``).
        docker_runner: Required when ``lane == "functional"``.
        docker_run_kwargs: Forwarded to ``docker_runner.run(**kwargs)``.
            Must include at minimum ``repo_path``, ``scratch_path``,
            ``command``.

    Raises:
        LaneNotImplementedError: for any lane not in ``_MVP_LIT_LANES``.
        ValueError: when lane is functional but docker_runner is None.
    """
    # v0.1 alias compatibility — collapse old names to v0.2 lanes with warning
    if lane in _DEPRECATED_V01_ALIASES:
        import warnings

        new_lane = _DEPRECATED_V01_ALIASES[lane]
        warnings.warn(
            f"lane {lane!r} is a deprecated v0.1 alias; mapped to {new_lane!r}. "
            f"v0.3 will remove the alias. See Decision 2.",
            DeprecationWarning,
            stacklevel=2,
        )
        lane = new_lane

    if lane not in _MVP_LIT_LANES:
        phase = _LANE_PHASES.get(lane, "unknown lane")
        raise LaneNotImplementedError(
            f"lane {lane!r} is not supported in v0.2 — {phase}. "
            f"v0.2 lit lanes: {sorted(_MVP_LIT_LANES)}"
        )

    # All v0.2 lit lanes currently dispatch through the same DockerRunner
    # interface — the per-framework runner image is supplied by the caller
    # via docker_run_kwargs (see Task 6's Gen-Functional dispatcher). The
    # Browser + Integration lanes additionally need AppRuntime wired in
    # (Task 8); that integration lives in the Executor, not here.
    if docker_runner is None:
        raise ValueError(f"dispatch_lane({lane!r}) requires docker_runner")
    if not docker_run_kwargs:
        raise ValueError(
            f"dispatch_lane({lane!r}) requires docker_run_kwargs "
            f"(at minimum repo_path, scratch_path, command)"
        )
    run_result = docker_runner.run(**docker_run_kwargs)
    return DispatchResult(
        lane=lane,
        runner_used="docker",
        docker_result=run_result,
    )


def is_lane_lit(lane: str) -> bool:
    """Cheap check for whether a lane has a real runner.

    Accepts v0.1 aliases (functional/sast/dast/fuzz) — they're considered
    'lit' for compatibility through v0.2 (they map to 'unit' on dispatch).
    The RFC-0013 ``deploy`` lane is lit too (DRY-RUN deploy verification).
    """
    return lane in _MVP_LIT_LANES or lane in _DEPRECATED_V01_ALIASES or lane == DEPLOY_LANE


# ---------------------------------------------------------------------------
# Deploy-lane dispatch (RFC-0013 / #446) — DRY-RUN deploy verification
# ---------------------------------------------------------------------------

DEPLOY_LANE = "deploy"


def dispatch_deploy_lane(  # noqa: PLR0913 - explicit keyword-only deploy-lane knobs
    *,
    files: list[str],
    required_scans: list[str] | None = None,
    target_level: str = "VAL-2",
    spec_dir: Path | None = None,  # when set, persist the VAL block to findings/
    run_fn=None,  # injectable for tests; defaults to a real subprocess runner
    tool_available=None,  # injectable for tests; defaults to shutil.which
):
    """Dispatch the RFC-0013 ``deploy`` lane — DRY-RUN deploy verification only.

    Runs the dry-run deploy steps (``terraform plan``, ``helm template |
    kubeconform``, ``kubectl apply --dry-run=server``) and the IaC/container
    scans that apply to ``files``, then returns a :class:`DispatchResult` whose
    ``deploy_result`` carries the steps and the gate-normalized VAL block. A
    production apply is NEVER assembled or run (RFC-0013). Absent any deploy
    files this is a no-op (empty steps, honest not_run VAL block).

    When ``spec_dir`` is given, the gate-normalized verification block is
    persisted to ``<spec_dir>/findings/deploy_verification.json`` so the
    completion envelope / merge policy (``agents.deploy_policy``) can read it.
    """
    result = run_deploy_lane(
        files,
        required_scans=required_scans,
        target_level=target_level,
        run_fn=run_fn,
        tool_available=tool_available,
    )
    if spec_dir is not None:
        try:
            findings = Path(spec_dir) / "findings"
            findings.mkdir(parents=True, exist_ok=True)
            (findings / "deploy_verification.json").write_text(
                json.dumps(result.verification, indent=2)
            )
        except OSError:
            pass
    return DispatchResult(
        lane=DEPLOY_LANE,
        runner_used="deploy",
        deploy_result=result,
        notes=list(result.notes),
    )


# ---------------------------------------------------------------------------
# Browser-lane dispatch (Task 8 / #24) — AppRuntime lifecycle wrapper
# ---------------------------------------------------------------------------


def dispatch_browser_lane(
    *,
    target,  # DockerComposeTarget from tfactory_yml.schema
    repo_root: Path,
    docker_runner: DockerRunner,
    docker_run_kwargs: dict[str, Any],
    app_runtime_cls=None,  # injectable for tests; defaults to AppRuntime
    allow_private_targets: bool = False,  # SSRF guard opt-in (#359)
) -> DispatchResult:
    """Dispatch a Browser-lane (or Integration-lane) test with AppRuntime.

    Spins up the AIFactory app via docker-compose, waits for it to be
    healthy, runs the Playwright test container with ``TFACTORY_TARGET_URL``
    injected into the environment, then tears the app down.

    The canonical target URL (used as ``TFACTORY_TARGET_URL``) is the first
    entry in ``target.wait_for`` — this is the URL the test framework connects
    to.  If ``wait_for`` is empty the test is dispatched without a target URL
    (legacy / misconfigured case — callers should ensure ``wait_for`` is
    populated for Browser-lane subtasks).

    Args:
        target: A ``DockerComposeTarget`` instance.
        repo_root: Absolute path to the AIFactory project root.
        docker_runner: Configured ``DockerRunner`` pointing at the
            Playwright runner image.
        docker_run_kwargs: Forwarded to ``docker_runner.run(**kwargs)``.
            Must include at minimum ``repo_path``, ``scratch_path``,
            ``command``.  Any ``extra_env`` already present is merged
            (caller values take precedence except for
            ``TFACTORY_TARGET_URL`` which is always overridden by this
            function).
        app_runtime_cls: Injected in tests to replace ``AppRuntime``
            with a stub.  Defaults to the real ``AppRuntime``.

    Returns:
        ``DispatchResult`` with ``lane="browser"`` and the
        ``DockerRunResult`` from the Playwright run.

    Raises:
        ``AppRuntimeError``: if the docker-compose start or health-poll
            fails.  The app is always stopped before the error propagates
            (``AppRuntime`` context manager guarantees this).
    """
    from .app_runtime import AppRuntime

    runtime_cls = app_runtime_cls or AppRuntime

    # Merge extra_env: start from caller-supplied values, then overlay
    # TFACTORY_TARGET_URL so the test always knows which URL to hit.
    kwargs = dict(docker_run_kwargs)
    caller_extra_env: dict[str, str] = dict(kwargs.get("extra_env") or {})

    target_url: str | None = None
    if target.wait_for:
        target_url = target.wait_for[0].url

    # SSRF guard (#359): refuse to hand a cloud-metadata / link-local URL to
    # the network-enabled lane before any fetch happens. The compose app this
    # path targets runs on localhost, so loopback is expected; metadata /
    # link-local is blocked unconditionally. AppRuntime applies the same
    # check to its own health-poll.
    if target_url is not None:
        from .net_guard import assert_safe_target_url

        assert_safe_target_url(
            target_url,
            allow_private=allow_private_targets,
            allow_loopback=True,
        )

    with runtime_cls(target, repo_root, allow_private_targets=allow_private_targets) as runtime:
        runtime.wait_for_healthy()
        if target_url is not None:
            caller_extra_env["TFACTORY_TARGET_URL"] = target_url
        if caller_extra_env:
            kwargs["extra_env"] = caller_extra_env
        run_result = docker_runner.run(**kwargs)

    return DispatchResult(
        lane="browser",
        runner_used="docker",
        docker_result=run_result,
    )


# ---------------------------------------------------------------------------
# Kubernetes dispatch (#108) — kubectl port-forward lifecycle wrapper
# ---------------------------------------------------------------------------


def dispatch_kubernetes_lane(
    *,
    lane: str,
    target,  # KubernetesTarget from tfactory_yml.schema
    docker_runner: DockerRunner,
    docker_run_kwargs: dict[str, Any],
    kubeconfig: str | None = None,
    kube_runtime_cls=None,  # injectable for tests; defaults to KubernetesRuntime
) -> DispatchResult:
    """Dispatch a lane against a Kubernetes service via ``kubectl port-forward``.

    Port-forwards ``target.service`` for the run lifetime, injects the resolved
    ``http://localhost:<port>`` as ``TFACTORY_TARGET_URL`` into the test
    container env, runs the test, then tears the forward down (guaranteed by the
    ``KubernetesRuntime`` context manager, on success and on failure).

    Auth (ServiceAccount token / mTLS) is carried by ``kubeconfig`` — the
    read-only file ``sandbox_credentials`` materialises for egress lanes.

    Args:
        lane: The lane string this k8s target serves (e.g. ``"api"`` /
            ``"browser"`` / ``"integration"``) — recorded on the result.
        target: A ``KubernetesTarget`` with ``port_forward=True``.
        docker_runner: Configured ``DockerRunner`` for the framework image.
        docker_run_kwargs: Forwarded to ``docker_runner.run(**kwargs)``; any
            ``extra_env`` is merged (``TFACTORY_TARGET_URL`` is overridden here).
        kubeconfig: Path to the materialised kubeconfig (``--kubeconfig``).
        kube_runtime_cls: Injected in tests to replace ``KubernetesRuntime``.

    Raises:
        KubernetesRuntimeError: if the forward can't start or become ready; the
            forward is always torn down before the error propagates.
    """
    from .kubernetes_runtime import KubernetesRuntime

    runtime_cls = kube_runtime_cls or KubernetesRuntime

    kwargs = dict(docker_run_kwargs)
    caller_extra_env: dict[str, str] = dict(kwargs.get("extra_env") or {})

    with runtime_cls(target, kubeconfig=kubeconfig) as runtime:
        caller_extra_env["TFACTORY_TARGET_URL"] = runtime.target_url
        kwargs["extra_env"] = caller_extra_env
        run_result = docker_runner.run(**kwargs)

    return DispatchResult(
        lane=lane,
        runner_used="docker",
        docker_result=run_result,
    )

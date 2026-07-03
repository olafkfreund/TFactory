"""Evaluator agent — Task 7, issue #8.

Third agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads completed Lane.UNIT subtasks from test_plan.json, computes
five evaluation signals per generated test (coverage delta, 3× stability,
mutate-and-check, lint promotion + the LLM's semantic-relevance call),
hands them to an LLM via the evaluator.md prompt, then validates the
verdicts.json the LLM writes.

Browser-lane AppRuntime status transitions:

  The Evaluator surfaces two Browser-lane phases in status.json so
  the portal's LaneStatusGrid can show operators what is happening:

    ``executor_app_running``  — docker-compose services are up + healthy;
                                the Playwright container is executing.
    ``app_not_healthy``       — the AppRuntime health-poll timed out before
                                all ``wait_for`` URLs replied with their
                                expected HTTP status code.  The error
                                message includes the last observed status
                                code per URL.

  These phases are set by ``_run_browser_subtask_with_runtime()`` which
  wraps the AppRuntime + DockerRunner lifecycle for a single Browser-lane
  subtask.  ``run_evaluator`` calls this helper instead of the plain
  DockerRunner path when the subtask's lane is ``"browser"`` or
  ``"integration"``.

  Implementation note: the status transitions are thin wrappers — the
  heavy lifting (AppRuntime lifecycle, health-poll, TFACTORY_TARGET_URL
  injection) lives in ``tools/runners/app_runtime.py`` and
  ``tools/runners/lane_dispatch.py``.  The Evaluator only owns the
  *status.json* side-effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging as _logging
import os
import shutil
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents.run_result import RunResultLike

if TYPE_CHECKING:
    from tools.runners.docker_runner import DockerRunResult

# Cohesive helpers extracted to focused modules (issue #450, god-file split).
# Re-imported here so the in-module callers and the public/test import paths
# (agents.evaluator._validate_verdicts, ._resolve_target, ._kube_runtime_for,
# etc.) keep working unchanged after the split.
from agents.evaluator_targets import (
    _browser_target_url,
    _docker_run_runtime_for,
    _kube_runtime_for,
    _resolve_target,
    _test_credential_specs,
)
from agents.evaluator_verdicts import _validate_verdicts
from agents.nix_env import (
    environment_from_contract,
    is_nix_environment,
    run_pytest_lane_via_nix,
)
from agents.workspace_status import now_iso, read_status, write_status_patch

_eval_log = _logging.getLogger(__name__)

# Schema version stamped into verdicts.json for traceability of the verdicts
# document format the Evaluator produces. Bump when the verdicts.json shape
# changes; this is a contract version, not a commit-provenance string.
_VERDICTS_SCHEMA_VERSION = "1.0"
# Back-compat alias: external readers/tests referenced this name.
_EVALUATOR_VERSION = _VERDICTS_SCHEMA_VERSION


# ─── Workspace helpers — shared via agents.workspace_status (#451).
# Thin module-local aliases keep the existing call sites unchanged while the
# single shared implementation does the work; the stage discriminator
# ("evaluator") is bound here.


_now_iso = now_iso


def _read_status(spec_dir: Path) -> dict:
    return read_status(spec_dir)


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    write_status_patch(spec_dir, "evaluator", **fields)


# ─── SDK seams (mockable in tests) ──────────────────────────────────────


async def _resolve_evaluator_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the evaluation phase.

    Same pattern as ``planner._resolve_planner_client`` /
    ``gen_functional._resolve_client``. Heavy imports deferred to
    runtime so tests can mock this seam without the SDK chain.

    Uses the 'coding' phase model for now — same budget as
    Gen-Functional. A 'evaluation' phase can be added to phase_config
    once we know the right thinking-token budget. Conservative for now.
    """
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    eval_model = get_phase_model(spec_dir, "coding", None)
    provider_name = infer_provider_from_model(eval_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "coding")
        return create_client(
            project_dir,
            spec_dir,
            eval_model,
            max_thinking_tokens=thinking_budget,
        )
    extra = get_provider_extra_kwargs(provider_name, eval_model)
    # Ollama runs file ops through TFactory's ToolExecutor (sandboxed to
    # working_dir); the Evaluator reads/writes within the spec/workspace dir,
    # outside the SUT project — allow it explicitly. Other agentic providers
    # use their own sandboxes and don't take this kwarg.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase="coding",
        working_dir=project_dir,
        model=extra.pop("model", eval_model),
        **extra,
    )


async def _invoke_session(
    client,
    prompt: str,
    spec_dir: Path,
    verbose: bool,
) -> tuple[str, str, dict]:
    """Wrap run_agent_session so tests can patch one symbol."""
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client,
            prompt,
            spec_dir,
            verbose,
            phase=LogPhase.CODING,
        )


# ─── Runner-fn seam for stability + mutation primitives ─────────────────


# Shared structural result contract (extracted to agents/run_result.py, #426).
# Aliased to the historical local name so the annotation below stays unchanged.
_RunResultLike = RunResultLike


_PYTEST_IMAGE = "tfactory-runner-pytest:latest"


def _parse_marker_exit(stdout: str | None, prefix: str, default: int) -> int:
    """Recover the real exit code from an ``echo <prefix>$?`` marker line.

    Each runner wraps its test command in a shell that always exits 0, appending
    a ``<prefix><code>`` line so the true pass/fail survives. Returns the last
    parseable marker, or ``default`` when none is present. ``prefix`` includes
    the trailing ``=`` (e.g. ``"__PYTEST_EXIT="``).
    """
    code = default
    for line in (stdout or "").splitlines():
        if line.startswith(prefix):
            try:
                code = int(line.split("=", 1)[1])
            except ValueError:
                pass
    return code


# ── Host-execution fallback (no container runtime, e.g. k3d pods) ──────────
# TFactory's pytest lanes run in a hardened DockerRunner container. In a cluster
# whose pods have NO container runtime (k3d — the same constraint that forced
# AIFactory's gates onto k8s Jobs), DockerRunner can't spawn anything and tests
# never execute ("ModuleNotFoundError: pytest"). This fallback runs pytest on the
# host in a per-project venv (project deps + pytest installed once) so the
# generated tests actually run and produce verdicts/junit/coverage. It mirrors
# AIFactory's default *host* gate runner. Selection: TFACTORY_RUNNER_MODE =
# host|docker, else auto (host only when no runtime is available).
_HOST_VENVS: dict[str, Path] = {}


def _container_runtime_available() -> bool:
    import shutil

    return bool(
        shutil.which(os.environ.get("TFACTORY_CONTAINER_BIN", "docker"))
        or shutil.which("podman")
    )


def _host_runner_mode() -> bool:
    mode = os.environ.get("TFACTORY_RUNNER_MODE", "").strip().lower()
    if mode == "host":
        return True
    if mode == "docker":
        return False
    return not _container_runtime_available()  # auto: host when no runtime


def _nix_verify_mode(spec_dir: Path) -> bool:
    """Whether the pytest lane should run via the Nix k8s Job (RFC-0016 #469).

    nixjob is the DEFAULT verify execution path: ON when a Nix runner image is
    configured (``TFACTORY_NIX_RUNNER_IMAGE``) AND the spec's contract declares a
    nix environment — the toolchain then comes from the per-task flake, matching
    the build env with no drift. ``TFACTORY_VERIFY_BACKEND`` overrides:
    ``nixjob`` forces it on (even without a contract nix env, e.g. a repo-owned
    flake), ``docker``/``host`` force the legacy runner. If nixjob is selected but
    unavailable at run time, the caller falls back to host/docker — a config gap
    must never hard-fail the lane.
    """
    backend = os.environ.get("TFACTORY_VERIFY_BACKEND", "").strip().lower()
    if backend in ("docker", "host"):
        return False
    if backend == "nixjob":
        return True
    if not os.environ.get("TFACTORY_NIX_RUNNER_IMAGE"):
        return False
    try:
        return is_nix_environment(environment_from_contract(spec_dir))
    except Exception:  # noqa: BLE001 - any contract-read issue → legacy runner
        return False


def _maybe_nix_verify(
    spec_dir: Path,
    project_dir: Path,
    test_file: Path,
    extra_env: dict[str, str],
) -> DockerRunResult | None:
    """Run the pytest lane via the Nix k8s Job when selected, else None.

    Returns the DockerRunResult-shaped result on a real run, or None when nixjob
    isn't selected / is unavailable / errors — so the caller falls through to the
    legacy host/docker runner. Never raises (a config gap must not fail the lane).
    """
    if not _nix_verify_mode(spec_dir):
        return None
    try:
        nix_res = run_pytest_lane_via_nix(
            spec_dir, project_dir, test_file, extra_env=extra_env, timeout=300
        )
    except Exception as exc:  # noqa: BLE001 - never fail the lane on a config gap
        _eval_log.warning(
            "[evaluator] nixjob verify errored (%s); falling back to host/docker",
            exc,
        )
        return None
    if nix_res is None:
        _eval_log.info(
            "[evaluator] nixjob verify unavailable for %s; falling back to host/docker",
            Path(spec_dir).name,
        )
    return nix_res


def _stage_sut_into_scratch(scratch: Path, test_file: Path, project_dir: Path) -> None:
    """Copy the SUT into ``scratch`` and drop the specific test under ``tests/``.

    Done on the host (scratch is bind-mounted rw in the docker path) so the
    read-only /work mount + container-uid write constraints don't bite, and the
    whole tree is made world-writable for the non-root container uid. Shared by
    the host + docker pytest runners.
    """
    for item in Path(project_dir).iterdir():
        if item.name == ".git":
            continue
        dst = scratch / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    tdir = scratch / "tests"
    tdir.mkdir(exist_ok=True)
    shutil.copy2(test_file, tdir / Path(test_file).name)
    # The container runs as a non-root uid; make scratch world-writable.
    for p in scratch.rglob("*"):
        with contextlib.suppress(OSError):
            p.chmod(0o777)
    scratch.chmod(0o777)


def _ensure_host_venv(project_dir: Path) -> Path:
    """A per-project venv with the project's deps + pytest/pytest-cov (built once)."""
    import subprocess
    import tempfile
    import venv as _venv

    key = str(Path(project_dir).resolve())
    cached = _HOST_VENVS.get(key)
    if cached and (cached / "bin" / "python").exists():
        return cached
    vdir = Path(tempfile.mkdtemp(prefix="tf-hostvenv-"))
    _venv.create(vdir, with_pip=True)
    py = str(vdir / "bin" / "python")
    args = [py, "-m", "pip", "install", "-q", "pytest", "pytest-cov"]
    req = Path(project_dir) / "requirements.txt"
    if req.exists():
        args += ["-r", str(req)]
    subprocess.run(args, capture_output=True, text=True, timeout=600)
    # Install the SUT itself when it ships a pyproject.toml (best-effort). The
    # bare install pulls in the project's runtime deps AND registers the package
    # — covering src-layout repos that have no requirements.txt, so
    # ``from <pkg> import ...`` resolves in the venv. The ``[test]``/``[dev]``
    # passes then pull common test-only deps (e.g. httpx for FastAPI's
    # TestClient) that live in an optional-dependencies group. Each pass is
    # non-fatal: an undeclared extra simply fails without affecting the others,
    # and the src PYTHONPATH added by the runner still resolves bare-source
    # imports if every install fails (#613).
    if (Path(project_dir) / "pyproject.toml").exists():
        pdir = str(project_dir)
        for target in (pdir, f"{pdir}[test]", f"{pdir}[dev]"):
            subprocess.run(  # noqa: S603 — fixed pip argv, no untrusted input
                [py, "-m", "pip", "install", "-q", "-e", target],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
    _HOST_VENVS[key] = vdir
    return vdir


def _run_pytest_on_host(
    scratch: Path, test_file: Path, extra_env: dict, project_dir: Path
):
    """Run one pytest file on the host (no container) — same result shape as the
    DockerRunner path. Used when no container runtime is available."""
    import subprocess

    from tools.runners.docker_runner import DockerRunResult

    vdir = _ensure_host_venv(project_dir)
    # Use the venv python as-is — do NOT resolve() the symlink, which would
    # jump past the venv to the base interpreter and lose the venv's
    # site-packages (pytest/pytest-cov would then be missing).
    py = str(vdir / "bin" / "python")
    name = Path(test_file).name
    cmd = (
        f"cd {scratch} && {py} -m pytest tests/{name} -p no:cacheprovider -q "
        "--junitxml=junit.xml --cov-report=xml:coverage.xml --cov=. 2>&1; "
        "echo __PYTEST_EXIT=$?"
    )
    scratch_root = Path(scratch).resolve()
    pythonpath = str(scratch_root)
    src_dir = scratch_root / "src"
    if src_dir.is_dir():
        pythonpath = f"{src_dir}{os.pathsep}{pythonpath}"
    env = {**os.environ, **extra_env, "PYTHONPATH": pythonpath}
    res = subprocess.run(
        ["sh", "-c", cmd], capture_output=True, text=True, timeout=300, env=env
    )
    code = _parse_marker_exit(res.stdout, "__PYTEST_EXIT=", res.returncode)
    junit = Path(scratch) / "junit.xml"
    cov = Path(scratch) / "coverage.xml"
    return DockerRunResult(
        returncode=code,
        stdout=res.stdout,
        stderr=res.stderr,
        junit_xml_path=junit if junit.exists() else None,
        coverage_xml_path=cov if cov.exists() else None,
        argv=["sh", "-c", cmd],
    )


def _resolve_runner_fn(
    spec_dir: Path,
    project_dir: Path,
    image: str = _PYTEST_IMAGE,
    network: str = "none",
    target_url: str | None = None,
    subtask: dict | None = None,
) -> Callable[[Path, Path, int], _RunResultLike]:
    """Return a callable matching the runner_fn seam for the pytest-based lanes
    (unit + api).

    ``runner_fn(test_file, project_dir, seed) -> DockerRunResult``. The given
    ``test_file`` may be the generated test (under the workspace) OR a mutated
    copy (under ``findings/mutants/``) — the runner copies the SUT and the
    specific test into a writable scratch dir on the host, then runs pytest
    inside it so ``from <module> import ...`` resolves (pyproject pythonpath).

    ``network``/``target_url`` support the **api** lane: pass ``network="host"``
    so the in-container test can reach a host-served app, and ``target_url`` to
    inject ``TFACTORY_TARGET_URL`` (httpx tests read it). The unit lane uses the
    defaults (``network="none"``, no target URL) for hermetic execution.

    Tests mock this whole function so the stability + mutation primitives can be
    exercised without Docker.
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        scratch = Path(_tmp.mkdtemp(prefix="tf-pytest-"))
        try:
            _stage_sut_into_scratch(scratch, test_file, project_dir_arg)

            runner = DockerRunner(image=image, network=network, read_only_rootfs=False)
            cmd = (
                "cd /scratch && "
                f"python -m pytest tests/{Path(test_file).name} "
                "-p no:cacheprovider -q --junitxml=/scratch/junit.xml "
                "--cov-report=xml:/scratch/coverage.xml --cov=. 2>&1; "
                "echo __PYTEST_EXIT=$?"
            )
            # ``/scratch/src`` first so src-layout packages import inside the
            # container; ``/scratch`` covers flat layouts. A missing path is
            # simply ignored by the interpreter.
            extra_env = {
                "PYTHONHASHSEED": str(seed),
                "PYTHONPATH": f"/scratch/src{os.pathsep}/scratch",
            }
            if target_url:
                extra_env["TFACTORY_TARGET_URL"] = target_url
                extra_env["APP_URL"] = target_url
            # Sandbox credential injection (#73): only the network-enabled api
            # lane (network != "none") gets broker-resolved creds, and only
            # when egress is opted in. Unit lane (network="none") gets neither.
            from tools.runners.sandbox_credentials import resolve_sandbox_credentials

            sandbox_creds = resolve_sandbox_credentials(
                project_dir_arg, spec_dir, network
            )
            extra_env.update(sandbox_creds.env)
            # Test-target login credentials (#107): a ref-auth target's
            # username/secret, resolved + injected as env (egress-gated like #73).
            from tools.runners.sandbox_credentials import (
                resolve_test_target_credentials,
            )

            test_creds = resolve_test_target_credentials(
                _test_credential_specs(spec_dir, subtask),
                project_dir_arg,
                spec_dir,
                network,
            )
            extra_env.update(test_creds.env)
            # RFC-0016 #469: nixjob is the DEFAULT verify path. Run the lane inside
            # the per-task Nix dev shell as a k8s Job (toolchain from the flake, no
            # drift). Returns None / falls through to the legacy host/docker runner
            # on any config gap — never hard-fails the lane. Creds are env-only here
            # and get wiped by whichever fallback path runs below.
            nix_res = _maybe_nix_verify(spec_dir, project_dir_arg, test_file, extra_env)
            if nix_res is not None:
                sandbox_creds.wipe()
                test_creds.wipe()
                return nix_res
            # No container runtime (k3d pod) → run pytest on the host instead, so
            # the generated tests actually execute and produce verdicts. (Same
            # result shape; secrets still wiped.)
            if _host_runner_mode():
                try:
                    return _run_pytest_on_host(
                        scratch, test_file, extra_env, project_dir_arg
                    )
                finally:
                    sandbox_creds.wipe()
                    test_creds.wipe()
            try:
                res = runner.run(
                    repo_path=Path(project_dir_arg).resolve(),
                    scratch_path=scratch.resolve(),
                    command=["sh", "-c", cmd],
                    extra_env=extra_env,
                    secret_files=sandbox_creds.files,
                    timeout_sec=300,
                )
            finally:
                sandbox_creds.wipe()  # erase materialised secret files
                test_creds.wipe()
            code = _parse_marker_exit(res.stdout, "__PYTEST_EXIT=", res.returncode)
            junit = scratch / "junit.xml"
            cov = scratch / "coverage.xml"
            return DockerRunResult(
                returncode=code,
                stdout=res.stdout,
                stderr=res.stderr,
                junit_xml_path=junit if junit.exists() else None,
                coverage_xml_path=cov if cov.exists() else None,
                argv=res.argv,
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


# ─── Per-test signal bundle ─────────────────────────────────────────────


@dataclass
class EvaluatorSignals:
    """Per-test bundle of the four pre-computed signals plus identity.

    The fifth signal (semantic relevance) is the LLM's call — it
    doesn't live in this dataclass.

    Any of the four signal fields can be ``None`` if the primitive
    couldn't run (e.g., coverage XML not emitted by the Executor for
    this test). The prompt helper renders missing signals as
    "not computed" rather than crashing.

    ``coverage_delta`` is explicitly ``None`` (not zero) when the
    framework's ``coverage_strategy == "skip"`` (Decision 11 — Browser
    lane).  This prevents the Evaluator prompt from seeing "0% coverage"
    and issuing a spurious reject for Playwright tests.  A ``None``
    value is rendered as "N/A (browser lane)" by
    ``_format_evaluator_per_test_block``.
    """

    test_id: str
    test_file: Path
    target: str
    rationale: str
    coverage_delta: Any = None  # CoverageDelta | None  (None = skip-coverage lane)
    stability: Any = None  # StabilityResult | None
    mutation: Any = None  # MutationResult | None
    lint_promotion: Any = None  # PromotionResult | None
    flaky_history: Any = None  # FlakyHistory | None  (cross-run flip-rate, #37)
    ci_parity: Any = None  # CIParityResult | None  (env-parity + real-imports, #302)


# ─── Signal-bundle assembly ─────────────────────────────────────────────


def _filter_completed_subtasks(plan: dict, predicate) -> list[dict]:
    """Return completed subtasks across all phases that match ``predicate``.

    Owns the shared phase/subtask walk plus the common
    ``status == 'completed'`` + ``files_to_create`` gate; ``predicate(subtask)``
    supplies the per-lane/language membership test.
    """
    return [
        st
        for phase in plan.get("phases", [])
        for st in phase.get("subtasks", [])
        if st.get("status") == "completed"
        and st.get("files_to_create")
        and predicate(st)
    ]


def _completed_functional_subtasks(plan: dict) -> list[dict]:
    """Pick subtasks that Gen-Functional successfully generated
    (status='completed', lane in {'unit','functional'}, has files_to_create).

    Accepts both the v0.2 'unit' lane and the v0.1 deprecated 'functional'
    alias so old test_plan.json files still process. v0.3 removes the
    'functional' alias. The pytest runner only handles Python, so a unit-lane
    subtask in another language (e.g. Jest/TypeScript) is excluded here.
    """
    return _filter_completed_subtasks(
        plan,
        lambda st: (
            st.get("lane") in ("unit", "functional")
            and st.get("language") in (None, "python")
        ),
    )


def _framework_coverage_strategy(subtask: dict) -> str | None:
    """Look up the framework descriptor's coverage_strategy for a subtask.

    Returns the strategy string ("lcov", "cobertura", "skip") or None
    if the subtask has no ``framework`` field or the registry lookup
    fails (e.g. unknown framework name — v0.1 back-compat).

    Failures are swallowed and logged at DEBUG level so a registry
    misconfiguration never blocks the Evaluator.
    """
    framework_name = subtask.get("framework")
    if not framework_name:
        return None
    try:
        from framework_registry import load_registry

        registry = load_registry()
        desc = registry.get(framework_name)
        if desc is None:
            _eval_log.debug(
                "coverage_strategy: framework %r not in registry; treating as numeric",
                framework_name,
            )
            return None
        return desc.coverage_strategy
    except Exception as exc:  # noqa: BLE001 — never block the Evaluator
        # Surface at WARNING: a registry misconfiguration would otherwise
        # silently skip the coverage strategy for every test in the run.
        _eval_log.warning(
            "coverage_strategy lookup failed for framework %r: %s — "
            "treating coverage as numeric",
            framework_name,
            exc,
        )
        return None


# ─── Browser-lane AppRuntime status transitions (Task 8 / #24) ──────────


def _run_browser_subtask_with_runtime(
    spec_dir: Path,
    subtask: dict,
    runner_fn=None,
    *,
    target=None,
    repo_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Execute a Browser-lane subtask wrapped in an AppRuntime lifecycle.

    Writes status.json phase transitions visible to the portal:

      ``executor_app_running`` — docker-compose is up + healthy; Playwright
                                  container is executing.
      ``app_not_healthy``      — health-poll timed out; the error message
                                  includes the last observed status code per
                                  URL.

    This function is intentionally side-effect-light — it owns ONLY the
    ``status.json`` writes.  The actual docker-compose / HTTP-poll / container
    execution lives in ``tools.runners.app_runtime`` and
    ``tools.runners.lane_dispatch``.

    Args:
        spec_dir: TFactory workspace spec directory.
        subtask: A subtask dict from test_plan.json (lane == "browser" or
            "integration").
        runner_fn: Injectable subprocess.run replacement for tests.
        target: A ``DockerComposeTarget`` instance.  When ``None`` this
            function is a no-op and returns ``(False, "no_target")``.
        repo_root: Absolute path to the AIFactory project root (required
            when ``target`` is not None).

    Returns:
        ``(success, error_phase)`` — where ``success=True`` means the
        Playwright container ran (its exit code is separate), and
        ``error_phase`` is set when AppRuntime itself failed (e.g.
        ``"app_not_healthy"``).
    """
    if target is None:
        # No DockerComposeTarget — Browser subtask with a static base_url;
        # skip AppRuntime lifecycle entirely.
        return False, "no_target"

    from tools.runners.app_runtime import AppRuntime, AppRuntimeError

    _write_status_patch(
        spec_dir,
        phase="executor_app_running",
        browser_subtask_id=subtask.get("id", ""),
    )

    runtime_kwargs: dict = {}
    if runner_fn is not None:
        runtime_kwargs["runner_fn"] = runner_fn

    try:
        with AppRuntime(target, repo_root, **runtime_kwargs) as runtime:
            try:
                runtime.wait_for_healthy()
            except AppRuntimeError as exc:
                _eval_log.error(
                    "app_not_healthy for subtask %s: %s",
                    subtask.get("id", ""),
                    exc,
                )
                _write_status_patch(
                    spec_dir,
                    phase="app_not_healthy",
                    app_runtime_error=str(exc)[:500],
                    browser_subtask_id=subtask.get("id", ""),
                )
                return False, "app_not_healthy"
            # App is healthy — caller proceeds with the test run.
            return True, None
    except AppRuntimeError as exc:
        # start() itself failed (compose up returned non-zero).
        _eval_log.error(
            "app_runtime start failed for subtask %s: %s",
            subtask.get("id", ""),
            exc,
        )
        _write_status_patch(
            spec_dir,
            phase="app_not_healthy",
            app_runtime_error=str(exc)[:500],
            browser_subtask_id=subtask.get("id", ""),
        )
        return False, "app_not_healthy"


def _coverage_delta_for_subtask(
    spec_dir: Path,
    subtask: dict,
):
    """Try to compute coverage delta for one test.

    Returns ``None`` in two distinct cases:

    1. **Skip-coverage lane** (Decision 11): the subtask's framework has
       ``coverage_strategy == "skip"`` (e.g. Playwright Browser lane).
       The Evaluator prompt renders this as "N/A (browser lane)" and does
       NOT penalise the test for zero coverage.

    2. **Coverage XML absent**: baseline or per-test coverage.xml are
       missing — the LLM will see "not computed" (pre-existing behaviour).

    Looks for ``spec_dir/findings/baseline_coverage.xml`` and
    ``spec_dir/findings/runs/<test_id>/coverage.xml`` for case (2).
    """
    # Case 1: framework explicitly opted out of coverage measurement.
    strategy = _framework_coverage_strategy(subtask)
    if strategy == "skip":
        _eval_log.debug(
            "coverage_delta: framework %r uses skip strategy — returning None",
            subtask.get("framework"),
        )
        return None

    # Case 2: try to parse XML coverage artefacts.
    from agents.coverage_delta import compute_delta_from_paths

    baseline = spec_dir / "findings" / "baseline_coverage.xml"
    after = spec_dir / "findings" / "runs" / subtask["id"] / "coverage.xml"
    if not baseline.exists() or not after.exists():
        return None
    try:
        # Parse by the framework's coverage format (jacoco for Java, Cobertura
        # otherwise) so non-Python lanes don't feed JaCoCo XML to the Cobertura
        # parser.
        return compute_delta_from_paths(baseline, after, fmt=strategy)
    except Exception as exc:  # noqa: BLE001 — defensive
        _eval_log.warning(
            "coverage_delta failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _stability_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the 3× stability check for one test."""
    from agents.stability_runner import check_stability

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        return check_stability(test_file, project_dir, runner_fn)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "stability check failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _flaky_history_for_subtask(spec_dir: Path, subtask: dict, stability):
    """Record this run's pass/fail outcome into the project-level flaky
    history store and return the updated FlakyHistory (#37).

    The outcome is derived from the 3× stability verdict: ``STABLE`` is a
    clean pass; anything else (flaky / consistent-fail / error) is a fail.
    Returns ``None`` when stability couldn't run, so we don't pollute the
    history with a phantom outcome. The store lives one level above the
    spec dir (``<workspace>/<project>/test_history.json``) so it persists
    across separate spec runs of the same project.
    """
    if stability is None:
        return None
    from agents.flaky_history import record_outcome
    from agents.stability_runner import StabilityVerdict

    try:
        store = spec_dir.parent.parent / "test_history.json"
        passed = stability.verdict == StabilityVerdict.STABLE
        return record_outcome(store, subtask["id"], passed)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "flaky-history record failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _mutation_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the mutate-and-check probe for one test, dispatched by language.

    Routes to the Python (mutmut-style AST) or TypeScript (Stryker) backend
    via ``mutation_dispatch`` (#41). Writes the mutant to
    ``spec_dir/findings/mutants/<test_id>.<ext>`` so the original test file
    stays clean. Returns ``None`` for languages with no wired backend.
    """
    from agents.mutation_dispatch import (
        is_mutation_supported,
        mutant_extension,
        run_language_mutation,
    )

    language = subtask.get("language")
    if not is_mutation_supported(language):
        return None
    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    ext = mutant_extension(language)
    mutant_path = spec_dir / "findings" / "mutants" / f"{subtask['id']}.{ext}"
    try:
        return run_language_mutation(
            language, test_file, project_dir, runner_fn, mutant_path=mutant_path
        )
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "mutate probe failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _lint_promotion_for_subtask(spec_dir: Path, subtask: dict):
    """Run flake_risk_lint + promote findings for one test."""
    from agents.flake_risk_lint import flake_risk_lint
    from agents.lint_promotion import promote_flake_findings

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        source = test_file.read_text()
    except OSError:
        return None
    result = flake_risk_lint(source)
    return promote_flake_findings(result, source)


def _ci_parity_for_subtask(spec_dir: Path, subtask: dict):
    """Compute the CI-parity signal (env-parity + real-imports) for one
    Python test (#302).

    ``env_parity=True`` because the unit/api Docker lanes grade through
    ``DockerRunner.run_pytest`` with ``ci_parity_env()`` applied. The
    real-imports facet is a static scan of the generated source against the
    subtask's ``target``.
    """
    from agents.ci_parity import compute_ci_parity

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        source = test_file.read_text()
    except OSError:
        return None
    return compute_ci_parity(
        source,
        subtask.get("target"),
        env_parity=True,
    )


def _build_signal_bundle(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
) -> EvaluatorSignals:
    """Run every available signal primitive against ``subtask`` and
    return a bundle the prompt helper can format."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=_coverage_delta_for_subtask(spec_dir, subtask),
        stability=stability,
        mutation=_mutation_for_subtask(spec_dir, project_dir, subtask, runner_fn),
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
        ci_parity=_ci_parity_for_subtask(spec_dir, subtask),
    )


# ─── Browser-lane signal computation (static base_url path) ─────────────
#
# Wires the browser lane into run_evaluator for the common static-URL case
# (an http target in .tfactory.yml — e.g. a deployed Pages site). The
# docker-compose AppRuntime path (_run_browser_subtask_with_runtime) remains
# for local-app targets; this path runs the generated Playwright test in the
# playwright runner image against the remote URL with --network=bridge and
# TFACTORY_TARGET_URL injected.

_PLAYWRIGHT_IMAGE = "tfactory-runner-playwright:latest"


def _completed_browser_subtasks(plan: dict) -> list[dict]:
    """Completed Playwright/browser subtasks Gen-Functional generated."""
    return _filter_completed_subtasks(plan, lambda st: st.get("lane") == "browser")


def _maybe_run_build(spec_dir: Path, project_dir: Path) -> None:
    """Run `.tfactory.yml` ``build:`` steps before the lanes (#233).

    Reads the snapshotted config (``context/tfactory_yml.json``); no-op when no
    build steps are declared. Best-effort: a build failure is logged + recorded
    in status.json (``build_failed``) but never raises — a downstream docker_run
    lane will then surface the missing image as a clear health failure (#234).
    """
    ctx = spec_dir / "context" / "tfactory_yml.json"
    if not ctx.exists():
        return
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return
    raw_steps = cfg.get("build") or []
    if not raw_steps:
        return
    try:
        from types import SimpleNamespace

        from tools.runners.build_runner import run_build_steps

        steps = [SimpleNamespace(**s) for s in raw_steps]
        result = run_build_steps(steps, repo_root=Path(project_dir))
        if not result.ok:
            _eval_log.error("build steps failed: %s", result.error)
            _write_status_patch(
                spec_dir, build_failed=True, build_error=result.error[:200]
            )
    except Exception as exc:  # noqa: BLE001 — build wiring must never crash the run
        _eval_log.warning("build step execution skipped: %s", exc)


def _stage_browser_test(spec_dir: Path, project_dir: Path, subtask: dict) -> None:
    """Copy the generated test from the workspace into the project checkout so
    the playwright runner (which mounts project_dir at /repo) can see it."""
    import shutil as _sh

    rel = subtask["files_to_create"][0]
    src = spec_dir / rel
    dst = Path(project_dir) / rel
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(src, dst)


def _stage_visual_baselines(
    spec_dir: Path | None, subtask: dict | None, dest: Path
) -> int:
    """Stage a target's accepted visual baselines into a browser run scratch (#109).

    Copies ``<spec_dir>/findings/visual_baselines/<target>/`` into ``dest`` so a
    generated ``toHaveScreenshot`` assertion compares against the portal-accepted
    baseline (with ``snapshotPathTemplate`` pointing there) instead of treating
    every capture as new. Returns the number of images staged (0 when none).
    Best-effort — never raises into the run.
    """
    if spec_dir is None:
        return 0
    try:
        from agents.evidence.visual_baseline import stage_baselines

        target_name = (subtask or {}).get("target_name") or "default"
        n = stage_baselines(spec_dir, target_name, dest)
        if n:
            _eval_log.info("staged %d visual baseline(s) for target %s", n, target_name)
        return n
    except Exception as exc:  # noqa: BLE001 — baseline staging must not break the run
        _eval_log.warning("visual baseline staging skipped: %s", exc)
        return 0


def _resolve_browser_runner_fn(
    target_url: str | None,
    image: str = _PLAYWRIGHT_IMAGE,
    *,
    spec_dir: Path | None = None,
    subtask: dict | None = None,
):
    """Return a runner_fn(test_file, project_dir, seed) -> DockerRunResult that
    runs ONE Playwright spec in the runner image against ``target_url``.

    Mirrors the proven invocation: world-writable scratch, copy /repo→/scratch,
    symlink node_modules to the image's global install, --network=bridge, and
    TFACTORY_TARGET_URL injected so the spec hits the deployed site. When the
    target uses ``auth: {type: ref}`` (#107), the login credential is resolved
    and injected as env so the spec's login fixture can authenticate.
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        # relative path of the spec inside the project checkout
        try:
            rel = str(
                Path(test_file).resolve().relative_to(Path(project_dir_arg).resolve())
            )
        except ValueError:
            # test_file lives in the workspace, not the checkout — use its
            # path relative to the workspace tests/ layout instead.
            rel = "/".join(Path(test_file).parts[-3:])
        scratch = Path(_tmp.mkdtemp(prefix="tf-pw-"))
        try:
            scratch.chmod(0o777)
            # Stage accepted visual baselines into the run so a generated
            # toHaveScreenshot assertion diffs against them (#109).
            _stage_visual_baselines(spec_dir, subtask, scratch)
            runner = DockerRunner(image=image, network="host", read_only_rootfs=False)
            # DockerRunner mounts the checkout read-only at /work and a
            # writable scratch at /scratch (the workdir). Stage the project
            # into scratch so node_modules (symlinked to the image's global
            # install) resolves and Playwright can write artifacts.
            staged = (
                "cp -r /work/. /scratch/ 2>/dev/null; "
                "ln -sfn /usr/lib/node_modules /scratch/node_modules; "
                "cd /scratch && "
                f"npx playwright test {rel} --reporter=junit "
                "--output=/scratch/pw-artifacts; "
                "echo __PW_EXIT=$?"
            )
            extra_env = {}
            if target_url:
                extra_env["TFACTORY_TARGET_URL"] = target_url
                extra_env["APP_URL"] = target_url
            # Test-target login credentials (#107): inject the ref-auth
            # target's username/secret so the spec's login fixture can sign in.
            # network="host" here, so the egress-gated resolver runs.
            from tools.runners.sandbox_credentials import (
                resolve_test_target_credentials,
            )

            test_creds = resolve_test_target_credentials(
                _test_credential_specs(spec_dir, subtask) if spec_dir else [],
                project_dir_arg,
                spec_dir,
                "host",
            )
            extra_env.update(test_creds.env)
            try:
                res = runner.run(
                    repo_path=Path(project_dir_arg).resolve(),
                    scratch_path=scratch.resolve(),
                    command=["sh", "-c", staged],
                    extra_env=extra_env,
                    timeout_sec=300,
                )
            finally:
                test_creds.wipe()
            # The wrapper shell always exits 0; recover the real playwright exit
            # from the __PW_EXIT marker so stability sees the true pass/fail.
            code = _parse_marker_exit(res.stdout, "__PW_EXIT=", res.returncode)
            return DockerRunResult(
                returncode=code, stdout=res.stdout, stderr=res.stderr, argv=res.argv
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def _browser_evidence_stability(spec_dir: Path, subtask: dict):
    """Stability for a browser subtask from the Nix-Job junit (findings/
    browser_evidence.json), or None when there's no evidence.

    This is the REAL browser-lane signal in k3d: the in-container DockerRunner
    path can't run, so the per-spec pass/fail the Nix Job already produced is what
    lets a passing UI test be ACCEPTED (and its acceptance criterion VERIFIED)
    rather than stuck flagged on an "error" stability.
    """
    import json as _json

    from agents.stability_runner import (
        StabilityResult,
        StabilityRun,
        StabilityVerdict,
    )

    ev_path = Path(spec_dir) / "findings" / "browser_evidence.json"
    if not ev_path.is_file():
        return None
    try:
        results = _json.loads(ev_path.read_text())
    except Exception:  # noqa: BLE001
        return None
    files = subtask.get("files_to_create") or []
    spec_name = Path(files[0]).name if files else ""
    if spec_name not in results:
        return None
    passed = bool(results[spec_name])
    verdict = StabilityVerdict.STABLE if passed else StabilityVerdict.CONSISTENT_FAIL
    # Represent the junit outcome as a real graded run (rc 0 pass / 1 fail) so
    # downstream sees a graded result, not "0 runs collected". The Nix Job IS the
    # run; we record its verdict rather than a 3x re-run.
    run = StabilityRun(
        returncode=0 if passed else 1,
        stdout_tail="browser lane graded from the Nix-Job junit (RFC-0005)",
    )
    return StabilityResult(verdict=verdict, runs=(run,), seed=0, rerun_count=1)


def _build_browser_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for a Browser-lane subtask: stability from the Nix-Job junit
    (the real signal in k3d) when available, else 3× Playwright via the runner;
    coverage skipped (Decision 11), mutation skipped (no TS mutation here)."""
    stability = _browser_evidence_stability(spec_dir, subtask)
    if stability is None:
        stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,  # browser lane — coverage_strategy == "skip"
        stability=stability,
        mutation=None,  # mutation not run for the browser lane
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── API-lane signal computation (httpx against a host-served app) ──────


def _completed_api_subtasks(plan: dict) -> list[dict]:
    """Completed api-lane subtasks (httpx tests) Gen-Functional generated."""
    return _filter_completed_subtasks(plan, lambda st: st.get("lane") == "api")


def _build_api_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for an api-lane subtask: 3× stability via pytest/httpx
    against the host-served app, coverage skipped (the SUT runs out-of-process),
    mutation skipped."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,  # api lane hits a running service — no line coverage
        stability=stability,
        mutation=None,
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
        ci_parity=_ci_parity_for_subtask(spec_dir, subtask),
    )


# ─── Jest-lane signal computation (TypeScript unit tests) ───────────────

_JEST_IMAGE = "tfactory-runner-jest:latest"


def _completed_jest_subtasks(plan: dict) -> list[dict]:
    """Completed unit-lane TypeScript (Jest) subtasks Gen-Functional generated.

    These sit in the unit lane like pytest, but are TypeScript — so they need
    the Jest runner, not pytest.
    """
    return _filter_completed_subtasks(
        plan,
        lambda st: (
            st.get("lane") in ("unit", "functional")
            and st.get("language") == "typescript"
        ),
    )


def _resolve_jest_runner_fn(image: str = _JEST_IMAGE):
    """Return a runner_fn(test_file, project_dir, seed) -> DockerRunResult that
    runs ONE Jest/TypeScript spec in the runner image.

    The SUT (.ts modules + jest.config + tsconfig) and the test are flattened
    into a writable scratch dir so the test's relative ``./module`` import
    resolves; node_modules is symlinked to the image's global install and
    NODE_PATH spans jest's nested deps (ts-jest requires jest-util).
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        scratch = Path(_tmp.mkdtemp(prefix="tf-jest-"))
        try:
            # Copy the SUT (.ts modules + jest/ts config) into scratch root, then
            # place the test at its ORIGINAL relative path (e.g. tests/x.test.ts)
            # so its relative import (`../slugify` from a tests/ subdir, or
            # `./slugify` from the root) resolves the same way it was authored.
            for item in Path(project_dir_arg).iterdir():
                if item.name in (".git", "node_modules"):
                    continue
                dst = scratch / item.name
                if item.is_dir():
                    _sh.copytree(item, dst, dirs_exist_ok=True)
                else:
                    _sh.copy2(item, dst)
            tparts = Path(test_file).parts
            if "tests" in tparts:
                rel = Path(*tparts[tparts.index("tests") :])  # tests/<...>/x.test.ts
            else:
                rel = Path(Path(test_file).name)
            dst_test = scratch / rel
            dst_test.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(test_file, dst_test)
            for p in scratch.rglob("*"):
                try:
                    p.chmod(0o777)
                except OSError:
                    pass
            scratch.chmod(0o777)

            runner = DockerRunner(image=image, network="none", read_only_rootfs=False)
            node_path = (
                "/usr/local/lib/node_modules:"
                "/usr/local/lib/node_modules/jest/node_modules"
            )
            cmd = (
                "ln -sfn /usr/local/lib/node_modules /scratch/node_modules; "
                "cd /scratch && "
                f"npx jest --ci --forceExit {rel.as_posix()} 2>&1; "
                "echo __JEST_EXIT=$?"
            )
            res = runner.run(
                repo_path=Path(project_dir_arg).resolve(),
                scratch_path=scratch.resolve(),
                command=["sh", "-c", cmd],
                extra_env={"NODE_PATH": node_path},
                timeout_sec=300,
            )
            code = _parse_marker_exit(res.stdout, "__JEST_EXIT=", res.returncode)
            return DockerRunResult(
                returncode=code, stdout=res.stdout, stderr=res.stderr, argv=res.argv
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def _build_jest_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for a Jest (TypeScript unit) subtask: 3× stability via
    Jest; coverage + mutation skipped in the demo path."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,
        stability=stability,
        mutation=None,
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── Go-lane signal computation (Go unit tests via the per-task Nix shell) ──
#
# Go ``_test.go`` files live next to the code they test, so there's no
# single-file staging like pytest/Jest — the generated tests are copied into
# the worktree at their repo-relative paths and ``gotestsum ./...`` runs the
# whole module inside the per-task Nix dev shell (RFC-0005 Tier A k8s Job).


def _completed_go_subtasks(plan: dict) -> list[dict]:
    """Completed unit-lane Go subtasks Gen-Functional generated.

    Like pytest/Jest these sit in the unit lane, but they're Go — so they run
    via the per-task Nix dev shell (gotestsum over the whole module), not the
    pytest runner. The pytest filter excludes them (language != python) and the
    Jest filter excludes them (language != typescript), so without this lane a
    Go subtask would be silently dropped.
    """
    return _filter_completed_subtasks(
        plan,
        lambda st: (
            st.get("lane") in ("unit", "functional") and st.get("language") == "go"
        ),
    )


def _stage_go_test(spec_dir: Path, project_dir: Path, subtask: dict) -> None:
    """Copy a Go subtask's generated ``_test.go`` files from the workspace into
    the worktree at their repo-relative paths, so ``go test ./...`` (which runs
    over the co-mounted worktree) sees them next to the code they exercise."""
    import shutil as _sh

    for rel in subtask.get("files_to_create") or []:
        src = spec_dir / rel
        if not src.exists():
            continue
        dst = Path(project_dir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(src, dst)


def _resolve_go_runner_fn(spec_dir: Path, project_dir: Path):
    """Return a runner_fn(test_file, project_dir, seed) -> DockerRunResult that
    runs the Go module's tests in the per-task Nix dev shell (gotestsum ./...).

    ``test_file`` is used only as the module-resolution hint (Go runs the whole
    module each call, since its ``_test.go`` files live next to the code). When
    the Nix-lane sandbox isn't configured (``TFACTORY_NIX_RUNNER_IMAGE`` unset)
    the lane returns a failing result so the gap surfaces honestly in the
    stability signal rather than silently passing.
    """
    from agents.nix_env import run_gotest_lane_via_nix
    from tools.runners.docker_runner import DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        # Prefer the repo-relative path as the hint so the module resolver finds
        # the right go.mod in a multi-module repo; fall back to the raw path.
        try:
            hint = Path(test_file).relative_to(spec_dir)
        except ValueError:
            hint = Path(test_file)
        res = run_gotest_lane_via_nix(spec_dir, Path(project_dir_arg), hint=hint)
        if res is not None:
            return res
        return DockerRunResult(
            returncode=1,
            stdout="",
            stderr="go nix lane unavailable: TFACTORY_NIX_RUNNER_IMAGE unset",
            argv=["nix", "develop", "--", "go", "test", "./..."],
        )

    return _run


def _build_go_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn, stability=None
) -> EvaluatorSignals:
    """Signal bundle for a Go unit subtask: 3× stability via gotestsum over the
    module; coverage_delta + mutation skipped in the demo path (the lane emits
    Cobertura coverage, but there's no before/after baseline to diff yet).

    ``stability`` may be precomputed and shared across the module's subtasks:
    ``go test ./...`` is module-wide, so every Go unit subtask in the same module
    has the IDENTICAL stability result — computing it once (see _build_all_bundles)
    avoids dispatching 3 redundant Nix Jobs PER subtask, where a single transient
    Job-dispatch error would otherwise flip the whole module to stability=ERROR
    and downgrade accept→flag. Falls back to a per-subtask compute when None."""
    if stability is None:
        stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,
        stability=stability,
        mutation=None,
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── Verdicts.json validation ───────────────────────────────────────────
#
# Extracted to agents.evaluator_verdicts (issue #450). Re-imported below so
# the public/test import paths (agents.evaluator._validate_verdicts etc.) and
# the in-module callers keep working unchanged.


def _advance_to_triager(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the Triager after evaluator's success path.

    Lazy import — same defensive shape as gen_functional's
    _advance_to_evaluator. Gated by ``TFACTORY_AUTO_TRIAGE`` (default
    ON; tests pin off).
    """
    try:
        from agents.triager import schedule_triager

        schedule_triager(spec_dir, project_dir, mode="initial")
    except ImportError as exc:
        _eval_log.warning(
            "could not auto-schedule triager: %s",
            exc,
        )


# ─── The agent itself ───────────────────────────────────────────────────


def _load_plan_or_fail(spec_dir: Path) -> dict | None:
    """Load + parse test_plan.json. Returns the plan dict, or None after
    writing an ``evaluator_failed`` status patch when missing/unparseable."""
    plan_path = spec_dir / "test_plan.json"
    if not plan_path.exists():
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase="evaluator_no_plan",
            evaluator_error="test_plan.json not found",
        )
        return None
    try:
        return json.loads(plan_path.read_text())
    except json.JSONDecodeError as exc:
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase="evaluator_plan_unparseable",
            evaluator_error=f"test_plan.json invalid: {exc}",
        )
        return None


def _write_empty_verdicts(spec_dir: Path, mode: str) -> None:
    """Write an empty verdicts.json + ``evaluated_empty`` status (no work case)."""
    verdicts_dir = spec_dir / "findings"
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    (verdicts_dir / "verdicts.json").write_text(
        json.dumps(
            {
                "evaluator_version": _VERDICTS_SCHEMA_VERSION,
                "mode": mode,
                "verdicts": [],
                "generated_at": _now_iso(),
            },
            indent=2,
        )
    )
    _write_status_patch(
        spec_dir,
        status="evaluated_empty",
        phase="evaluator_no_completed_subtasks",
        verdicts_count=0,
    )


def _build_kube_or_static_bundle(
    spec_dir, project_dir, st, *, make_runner, make_bundle
):
    """Build one signal bundle for a subtask whose target may be Kubernetes.

    When the subtask's target resolves to a kube runtime, port-forward it for
    the run lifetime (#108) and use ``runtime.target_url``; otherwise use the
    static target URL. ``make_runner(url)`` builds the runner_fn and
    ``make_bundle(runner_fn)`` builds the EvaluatorSignals bundle.
    """
    target = _resolve_target(spec_dir, st)
    rt = _kube_runtime_for(target)
    if rt is not None:
        with rt as runtime:
            return make_bundle(make_runner(runtime.target_url))
    # docker_run target (#233): run the (built) image for the lane lifetime,
    # health-poll, then tear down — like the kube path but for a single image.
    drt = _docker_run_runtime_for(target)
    if drt is not None:
        with drt as runtime:
            runtime.wait_for_healthy()
            return make_bundle(make_runner(runtime.target_url))
    target_url = _browser_target_url(spec_dir, st)
    _gate_target_health(spec_dir, st, target, target_url)
    return make_bundle(make_runner(target_url))


def _gate_target_health(spec_dir, subtask, target, target_url) -> None:
    """Probe a configured ``health_check`` before a static-target lane (#234).

    Best-effort: surfaces a clear "target unhealthy" warning + a status marker
    so a down deployment reads as a target problem, not an opaque test timeout.
    No-op when the target has no health_check. Never raises.
    """
    if not isinstance(target, dict) or not target.get("health_check") or not target_url:
        return
    try:
        from agents.health_gate import gate

        result = gate(target_url, target.get("health_check"))
        if not result.ok:
            _eval_log.warning(
                "target health check FAILED for %s (%s): %s — lane will likely "
                "fail; this is a target problem, not the test.",
                subtask.get("id"),
                result.url,
                result.detail,
            )
            _write_status_patch(
                spec_dir,
                target_unhealthy=True,
                target_health_detail=result.detail[:200],
            )
    except Exception as exc:  # noqa: BLE001 — gating must never break the run
        _eval_log.warning("health gate skipped: %s", exc)


def _build_all_bundles(spec_dir, project_dir, unit, browser, api, jest, go) -> list:
    """Compute the per-test signal bundle for every completed subtask.

    runner_fn is the mockable seam — tests pass canned results so Docker isn't
    required. Browser/api targets may be Kubernetes (port-forwarded, #108).
    Go runs the whole module in the per-task Nix dev shell (gotestsum ./...).
    """
    bundles = []
    if unit:
        unit_runner = _resolve_runner_fn(spec_dir, project_dir)
        bundles += [
            _build_signal_bundle(spec_dir, project_dir, st, unit_runner) for st in unit
        ]
    for st in browser:
        # Stage the generated spec into the checkout so the Playwright runner
        # (mounts project_dir at /repo) can see it.
        _stage_browser_test(spec_dir, project_dir, st)
        bundles.append(
            _build_kube_or_static_bundle(
                spec_dir,
                project_dir,
                st,
                make_runner=lambda url, st=st: _resolve_browser_runner_fn(
                    url, spec_dir=spec_dir, subtask=st
                ),
                make_bundle=lambda runner, st=st: _build_browser_signal_bundle(
                    spec_dir, project_dir, st, runner
                ),
            )
        )
    for st in api:
        # network="host" so the in-container httpx test can reach the
        # host-served app at the target URL (e.g. http://localhost:8200).
        bundles.append(
            _build_kube_or_static_bundle(
                spec_dir,
                project_dir,
                st,
                make_runner=lambda url, st=st: _resolve_runner_fn(
                    spec_dir, project_dir, network="host", target_url=url, subtask=st
                ),
                make_bundle=lambda runner, st=st: _build_api_signal_bundle(
                    spec_dir, project_dir, st, runner
                ),
            )
        )
    if jest:
        jest_runner = _resolve_jest_runner_fn()
        bundles += [
            _build_jest_signal_bundle(spec_dir, project_dir, st, jest_runner)
            for st in jest
        ]
    if go:
        # Stage every generated _test.go into the worktree first, then run the
        # module: `go test ./...` must see all of them on every stability pass.
        for st in go:
            _stage_go_test(spec_dir, project_dir, st)
        go_runner = _resolve_go_runner_fn(spec_dir, project_dir)
        # `go test ./...` is MODULE-WIDE, so its 3× stability result is identical
        # for every Go unit subtask in the module. Compute it ONCE and share it,
        # rather than 3 Nix Jobs PER subtask (12 for 4 subtasks) — fewer dispatches
        # are faster and far less likely to hit a transient Job-dispatch error that
        # flips the whole module to stability=ERROR (accept→flag). Use the first
        # subtask's test_file as the representative; a None result (test missing /
        # sandbox unconfigured) falls back to a per-subtask compute below.
        shared_go_stability = (
            _stability_for_subtask(spec_dir, project_dir, go[0], go_runner)
            if go
            else None
        )
        bundles += [
            _build_go_signal_bundle(
                spec_dir, project_dir, st, go_runner, stability=shared_go_stability
            )
            for st in go
        ]
    return bundles


async def _run_evaluator_session(spec_dir, project_dir, bundles, verbose) -> bool:
    """Invoke the LLM with the signal bundles, then validate verdicts.json.

    Returns True on success (status → ``evaluated`` and Triager scheduled),
    False on a session error or invalid verdicts (status → ``evaluator_failed``).
    """
    from prompts_pkg.prompts import get_tfactory_evaluator_prompt

    prompt = get_tfactory_evaluator_prompt(spec_dir, project_dir, bundles)
    client = await _resolve_evaluator_client(spec_dir, project_dir)
    try:
        session_status, _response, _err = await _invoke_session(
            client,
            prompt,
            spec_dir,
            verbose,
        )
    except Exception as exc:  # noqa: BLE001 — surface in status
        _eval_log.error("evaluator session raised: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase="evaluator_session_error",
            evaluator_error=str(exc)[:500],
        )
        return False

    verdicts_path = spec_dir / "findings" / "verdicts.json"
    ok, err, count = _validate_verdicts(verdicts_path)
    if not ok:
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase="evaluator_invalid_verdicts",
            evaluator_error=err,
        )
        return False

    # Stamp deterministic confidence + flaky-history onto each verdict + a
    # run-level rollup (#238, #239). Best-effort: a scoring hiccup must never
    # fail an otherwise valid evaluation — the categorical verdicts already
    # passed validation. The flaky map (cross-run flip-rate) comes from the
    # signal bundles and drives both the confidence penalty and the
    # accept→flag override inside enrich_verdicts.
    try:
        from agents.confidence import enrich_verdicts

        flaky_by_test_id = {}
        for b in bundles:
            fh = getattr(b, "flaky_history", None)
            if fh is not None:
                try:
                    flaky_by_test_id[b.test_id] = fh.as_dict()
                except Exception:  # noqa: BLE001 — skip a malformed entry
                    _eval_log.debug(
                        "evaluator: skipping malformed flaky_history for %s",
                        b.test_id,
                        exc_info=True,
                    )
        doc = json.loads(verdicts_path.read_text())
        enrich_verdicts(doc, flaky_by_test_id)
        # Honor the RFC-0002 contract execution scope (#247): record declared
        # coverage_target / mutation_scope / security_scope into the run output.
        try:
            from agents.contract_scope import apply_execution_scope
            from agents.task_contract import read_tfactory_profile

            apply_execution_scope(doc, read_tfactory_profile(spec_dir))
        except Exception as exc:  # noqa: BLE001 — scope is additive metadata
            _eval_log.warning("contract scope skipped: %s", exc)
        verdicts_path.write_text(json.dumps(doc, indent=2))
    except Exception as exc:  # noqa: BLE001 — confidence is additive metadata
        _eval_log.warning("confidence/flaky enrichment skipped: %s", exc)

    _write_status_patch(
        spec_dir,
        status="evaluated",
        phase="evaluator_complete",
        verdicts_count=count,
        tests_evaluated=len(bundles),
    )
    # Forward-chain to the Triager (Task 8, #9). Gated by ``TFACTORY_AUTO_TRIAGE``
    # env; tests pin it off to keep this layer deterministic.
    _advance_to_triager(spec_dir, project_dir)
    return True


def _equivalence_lane_enabled() -> bool:
    return os.getenv("TFACTORY_EQUIVALENCE_LANE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _maybe_run_equivalence_lane(spec_dir: Path, project_dir: Path) -> None:
    """Run the RFC-0010 equivalence lane when enabled + the contract declares one.

    Reads the signed contract from ``context/task_contract.json``; no-op when the
    flag is off, the contract is absent, or it carries no ``tfactory.equivalence``
    block. Best-effort — a failure here never fails the verify.
    """
    if not _equivalence_lane_enabled():
        return
    try:
        contract_path = Path(spec_dir) / "context" / "task_contract.json"
        if not contract_path.is_file():
            return
        contract = json.loads(contract_path.read_text())
        if not ((contract.get("tfactory") or {}).get("equivalence")):
            return
        from agents.equivalence_lane import run_from_spec

        result = run_from_spec(spec_dir, project_dir, contract)
        if result is not None:
            _eval_log.info("equivalence lane: %s", result.get("claim"))
    except Exception as exc:  # noqa: BLE001 - equivalence is best-effort
        _eval_log.warning("equivalence lane skipped: %s", exc)


async def run_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Evaluator agent.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root (passed to docker runner +
            available to the LLM via Read/Glob/Grep).
        mode: 'initial' on first run; 'rerun' if invoked after a
            Triager-requested re-evaluation. Reserved — both modes
            currently share behaviour but the value is surfaced in
            status.json + verdicts.json for traceability.
        verbose: forwarded to ``run_agent_session``.

    Returns:
        True on a clean evaluation pass (including empty-test case);
        False on hard failure.

    Status transitions:
      generated   → evaluating          (in-flight marker)
                  → evaluated            (verdicts.json validated)
                  → evaluated_empty     (no tests to evaluate)
                  → evaluator_failed    (validation / session error)
    """
    try:
        _write_status_patch(
            spec_dir,
            status="evaluating",
            phase=f"evaluator_{mode}_started",
        )

        # 1. Load the plan + filter to completed subtasks per lane.
        plan = _load_plan_or_fail(spec_dir)
        if plan is None:
            return False

        unit_completed = _completed_functional_subtasks(plan)
        browser_completed = _completed_browser_subtasks(plan)
        api_completed = _completed_api_subtasks(plan)
        jest_completed = _completed_jest_subtasks(plan)
        go_completed = _completed_go_subtasks(plan)
        completed = (
            unit_completed
            + browser_completed
            + api_completed
            + jest_completed
            + go_completed
        )

        # 2. No work — early exit with evaluated_empty.
        if not completed:
            _write_empty_verdicts(spec_dir, mode)
            return True

        # 2b. Build the artifact under test if the config declares build steps
        #     (#233) — e.g. docker build an image a docker_run target then runs.
        _maybe_run_build(spec_dir, project_dir)

        # 2c. RFC-0005 Tier A browser evidence: when the contract declares a nix
        #     env and there's a browser lane, run that lane in a Nix k8s Job and
        #     collect screenshots into findings/. Additive — never blocks the
        #     verdict pipeline; a no-op when the Nix-lane sandbox isn't configured
        #     (TFACTORY_NIX_RUNNER_IMAGE unset) or there's no browser lane. Runs
        #     off the evaluator's loop (the sandbox uses asyncio.run internally).
        if browser_completed:
            try:
                from agents.nix_env import run_browser_evidence

                ev = await asyncio.to_thread(
                    run_browser_evidence, spec_dir, project_dir
                )
                if ev is not None:
                    _eval_log.info(
                        "nix browser evidence: ok=%s screenshots=%d",
                        ev["ok"],
                        len(ev["screenshots"]),
                    )
                    _write_status_patch(
                        spec_dir,
                        browser_evidence={
                            "ok": ev["ok"],
                            "screenshots": len(ev["screenshots"]),
                            "serve_command": ev["serve_command"],
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - evidence is best-effort
                _eval_log.warning("nix browser evidence failed (non-blocking): %s", exc)

        # 3. Per-test signal computation (real primitives; runner_fn seam
        #    mocked in tests so docker isn't required).
        bundles = _build_all_bundles(
            spec_dir,
            project_dir,
            unit_completed,
            browser_completed,
            api_completed,
            jest_completed,
            go_completed,
        )

        # 4-5. Invoke the SDK session + validate the verdicts it wrote.
        ok = await _run_evaluator_session(spec_dir, project_dir, bundles, verbose)
        # 6. RFC-0010 differential/equivalence lane (opt-in). Runs the legacy
        #    oracle vs the new impl over the golden corpus and merges its VAL-2
        #    verdicts. Guarded by env + the contract carrying an equivalence
        #    block; best-effort and never fatal to the verify.
        _maybe_run_equivalence_lane(spec_dir, project_dir)
        return ok

    except Exception as exc:
        _eval_log.error("evaluator failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase=f"evaluator_{mode}_exception",
            evaluator_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as _BG_PLANNER_TASKS and _BG_GEN_FUNCTIONAL_TASKS.
# Gen-Functional's success path (status=generated, tests_generated >= 1)
# calls schedule_evaluator after writing the status — gated on env so the
# test suite stays deterministic.

_BG_EVALUATOR_TASKS: set[asyncio.Task] = set()


def schedule_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Evaluator, gated by ``TFACTORY_AUTO_EVALUATE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``TFACTORY_AUTO_EVALUATE=0`` to keep gen_functional's success path
    from auto-advancing.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-evaluation. Each scheduled task is anchored in
    ``_BG_EVALUATOR_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("TFACTORY_AUTO_EVALUATE", "1") == "0":
        return None
    task = asyncio.create_task(run_evaluator(spec_dir, project_dir, mode=mode))
    _BG_EVALUATOR_TASKS.add(task)
    task.add_done_callback(_BG_EVALUATOR_TASKS.discard)
    return task

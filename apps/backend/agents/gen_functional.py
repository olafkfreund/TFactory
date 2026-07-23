"""Gen-Functional agent — Task 6, issue #7 (v0.1) / issue #22 (v0.2).

Second agent in the six-agent TFactory pipeline (Planner ← Gen-Functional →
Executor → Evaluator → Triager). Reads the Planner's emitted
`test_plan.json`, generates test code for each ``Lane.UNIT`` subtask
via the Claude Agent SDK, runs two MVP guardrails per subtask
(pre-flight static check + flake-risk lint), and either commits the
test file or writes a ``context/replan_request.json`` for the Planner.

**v0.2 (Task 6 / #22) additions:**

  - Per-subtask framework lookup via ``framework_registry.get_descriptor``.
  - The framework descriptor is forwarded to
    ``get_tfactory_gen_functional_prompt`` which injects the framework's
    ``context_block`` into the generic prompt body (replacing the
    Python-specific v0.1 prompt for polyglot subtasks).
  - ``_resolve_runner_fn`` now reads the image from
    ``framework_descriptor.runtime.image`` instead of the hardcoded
    ``tfactory-runner-python:latest`` constant.
  - v0.1-style subtasks (``subtask.framework is None``) degrade
    gracefully with a ``DeprecationWarning`` on both the prompt helper
    and the runner-image paths.

v0.1 Task 6 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Pre-flight static check (subprocess introspection)
  ✓ commit 3 — Flake-risk lint (AST patterns)
  ✓ commit 4 — gen_functional.md prompt + assembly helper
  ✓ commit 5 — Real run_gen_functional with SDK + guards + replan_request
  ✓ commit 6 — Integration test + close #7
"""

import asyncio
import json
import logging as _logging
import os
import traceback
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents.workspace_status import now_iso, read_status, write_status_patch

if TYPE_CHECKING:
    from test_plan import ImplementationPlan, Subtask

_gen_log = _logging.getLogger(__name__)


# ─── Workspace helpers — shared via agents.workspace_status (#451).
# Thin module-local aliases keep the existing call sites unchanged while the
# single shared implementation does the work; the stage discriminator
# ("gen_functional") is bound here.


_now_iso = now_iso


def _read_status(spec_dir: Path) -> dict:
    return read_status(spec_dir)


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    write_status_patch(spec_dir, "gen_functional", **fields)


# ─── The agent itself ─────────────────────────────────────────────────────

# ─── SDK seams (mockable in tests) ──────────────────────────────────────


# RFC-0014 runtime -> TFactory test-gen provider. Only runtimes that map to a
# provider TFactory can actually generate tests with appear here; the gated
# fan-out runtimes (claude-subagents, dynamic-workflow, antigravity) have no
# TFactory provider and are intentionally absent, so they never override the
# provider inferred from the model string.
_RUNTIME_TO_PROVIDER: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "ollama": "ollama",
    "ollama-cloud": "ollama",
}


def _apply_runtime_override(
    inferred_provider: str,
    model: str,
    runtime: str | None,
) -> str:
    """Optionally override the model-inferred provider with the routed runtime.

    Only overrides when the runtime maps to a TFactory-supported test-gen
    provider *and* the model string was provider-ambiguous — i.e. inference fell
    back to its ``"claude"`` default for a model that is not actually a Claude
    model (no ``claude``/``opus``/``sonnet``/``haiku`` signal). An explicitly
    Claude model, an already-provider-prefixed model, or an unmapped/gated
    runtime leaves the inferred provider untouched (back-compat).
    """
    if runtime is None:
        return inferred_provider
    mapped = _RUNTIME_TO_PROVIDER.get(runtime)
    if mapped is None or mapped == inferred_provider:
        return inferred_provider
    # Only disambiguate; never override a confidently-inferred provider.
    if inferred_provider != "claude":
        return inferred_provider
    lowered = model.strip().lower()
    is_explicit_claude = lowered.startswith("claude-") or lowered in {
        "opus",
        "opus-1m",
        "opus-4.5",
        "opus-4.7",
        "sonnet",
        "haiku",
    }
    if is_explicit_claude:
        return inferred_provider
    return mapped


async def _resolve_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the generation phase.

    Same pattern as planner._resolve_planner_client — heavy imports
    deferred to runtime so tests can mock this seam without the SDK chain.
    """
    from agents.model_routing import (  # noqa: PLC0415 - lazy by design
        routed_test_gen_model,
        runtime_from_contract,
    )
    from agents.task_contract import (  # noqa: PLC0415 - lazy by design
        read_task_contract,
    )
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
        resolve_model_id,
    )
    from providers.factory import get_provider

    # RFC-0014: prefer the cost-aware router's routed ``test_gen`` model from the
    # contract's ``execution.phase_models`` (a cheap-but-capable model the router
    # picked for test generation). Degrade to today's behaviour — the "coding"
    # phase model — when the contract has no routed entry.
    contract = read_task_contract(spec_dir)
    routed_model = routed_test_gen_model(contract)
    # The routed value is a shorthand/provider-prefixed string; resolve it to a
    # full model ID exactly as get_phase_model would for the fallback path.
    gen_model = (
        resolve_model_id(routed_model)
        if routed_model is not None
        else get_phase_model(spec_dir, "coding", None)
    )
    # The provider is inferred from the model string (its prefix carries the
    # provider). Honour the declared ``execution.runtime`` only when (a) a model
    # was routed, (b) the runtime maps to a TFactory-supported test-gen provider,
    # and (c) the model string is otherwise provider-ambiguous (inference fell
    # through to its env/"claude" default for a non-claude model). Gated runtimes
    # with no TFactory provider (claude-subagents, dynamic-workflow, antigravity)
    # are recorded but never override the inferred provider — back-compat.
    provider_name = infer_provider_from_model(gen_model)
    if routed_model is not None:
        provider_name = _apply_runtime_override(
            provider_name, gen_model, runtime_from_contract(contract)
        )
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "coding")
        return create_client(
            project_dir,
            spec_dir,
            gen_model,
            max_thinking_tokens=thinking_budget,
        )
    extra = get_provider_extra_kwargs(provider_name, gen_model)
    # Ollama runs file ops through TFactory's ToolExecutor (sandboxed to
    # working_dir). Generated tests are written into the spec/workspace dir,
    # outside the SUT project — allow it explicitly. Other agentic providers
    # use their own sandboxes and don't take this kwarg.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase="coding",
        working_dir=project_dir,
        model=extra.pop("model", gen_model),
        **extra,
    )


# #792: wall-clock ceiling on ONE gen-functional agent session. A hung LLM/tool
# session (seen: a gcd spec frozen 23 min in `generating`) otherwise strands the
# spec until the liveness sweep flips it ~10 min later; bound it shorter than the
# sweep deadline (APP_LIVENESS_SWEEP_DEADLINE_SECONDS, default 600s) so a stuck
# session fails FAST into the existing `status=error` path (fail the subtask +
# continue) instead of hanging. Generous vs a normal ~1-3 min session; tune with
# TFACTORY_GEN_SESSION_TIMEOUT_S.
_GEN_SESSION_TIMEOUT_S = float(os.environ.get("TFACTORY_GEN_SESSION_TIMEOUT_S", "480"))


async def _run_bounded(
    coro: Awaitable[tuple[str, str, dict[str, Any]]], *, timeout_s: float
) -> tuple[str, str, dict[str, Any]] | None:
    """Await ``coro`` with a wall-clock ceiling; return its result, or ``None``
    on timeout (#792). Pure utility — the timeout unit tests exercise THIS,
    immune to the SDK-session mocking that stubs ``_invoke_session`` wholesale.
    On timeout ``asyncio.wait_for`` cancels ``coro``; the caller's
    ``async with client`` then closes the transport (kills the agent subprocess).
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except TimeoutError:
        return None


async def _invoke_session(
    client,
    prompt: str,
    spec_dir: Path,
    verbose: bool,
) -> tuple[str, str, dict]:
    """Wrap run_agent_session so tests can patch one symbol.

    Bounded by ``_GEN_SESSION_TIMEOUT_S`` (#792): a hung session fails fast into
    the existing ``status="error"`` path (fail the subtask + continue) instead of
    stranding the spec at ``generating`` until the liveness sweep flips it.
    """
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        result = await _run_bounded(
            run_agent_session(
                client,
                prompt,
                spec_dir,
                verbose,
                phase=LogPhase.CODING,
            ),
            timeout_s=_GEN_SESSION_TIMEOUT_S,
        )
        if result is None:
            _gen_log.warning(
                "gen_functional: session exceeded %.0fs wall-clock — treating as "
                "error so the subtask fails fast instead of stranding (#792)",
                _GEN_SESSION_TIMEOUT_S,
            )
            return (
                "error",
                f"gen_functional session timed out after {_GEN_SESSION_TIMEOUT_S:.0f}s",
                {},
            )
        return result


# ─── Workspace helpers ──────────────────────────────────────────────────


def _files_to_create(subtask) -> list[str]:
    """Subtask.files_to_create may be a list (dataclass) or list-via-dict."""
    f = getattr(subtask, "files_to_create", None)
    if f is None and isinstance(subtask, dict):
        f = subtask.get("files_to_create")
    return list(f or [])


def _write_replan_request(
    spec_dir: Path,
    subtask_id: str,
    reason: str,
    failed_target: str,
) -> None:
    """Write context/replan_request.json for the Planner's replan mode.

    Schema matches what the planner_replan.md prompt + the planner's
    _load_replan_request helper expect: {subtask_id, reason, failed_target}.
    """
    rr = spec_dir / "context" / "replan_request.json"
    rr.parent.mkdir(parents=True, exist_ok=True)
    rr.write_text(
        json.dumps(
            {
                "subtask_id": subtask_id,
                "reason": reason,
                "failed_target": failed_target,
                "rejected_at": _now_iso(),
            },
            indent=2,
        )
    )


def _advance_to_planner_replan(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the Planner in replan mode after a guardrail rejection.

    Lazy import so a circular gen_functional ↔ planner can't form. Same
    GC-anchor pattern as the planner's own _advance_to_gen_functional.
    """
    try:
        from agents.planner import schedule_planner

        schedule_planner(spec_dir, project_dir, mode="replan")
    except ImportError as exc:
        _gen_log.warning(
            "could not auto-schedule planner replan: %s",
            exc,
        )


def _resolve_runner_fn(framework_descriptor=None):
    """Return a runner callable parameterized by the framework's Docker image.

    In v0.2 the runner image is taken from
    ``framework_descriptor.runtime.image``.  For v0.1-style subtasks
    (``framework_descriptor=None``) the legacy default image
    ``tfactory-runner-python:latest`` is used and a ``DeprecationWarning``
    is emitted.

    The returned callable has the signature::

        runner_fn(test_file: Path, project_dir: Path, seed: int) -> RunResultLike

    matching the seam that ``stability_runner.check_stability`` and
    ``mutate_probe.mutate_and_probe`` expect.

    Args:
        framework_descriptor: A ``FrameworkDescriptor`` instance, or ``None``
            for v0.1-style subtasks.

    Returns:
        A callable that runs a test file via DockerRunner.

    Note:
        Heavy imports are deferred so tests can patch this function without
        pulling in the full Docker runtime chain.
    """
    import warnings

    _DEFAULT_IMAGE = "tfactory-runner-python:latest"

    if framework_descriptor is None:
        warnings.warn(
            f"_resolve_runner_fn: framework_descriptor not provided; "
            f"falling back to default image {_DEFAULT_IMAGE!r}. "
            "Pass framework_descriptor for polyglot runner dispatch; "
            "this default will be removed in v0.3.",
            DeprecationWarning,
            stacklevel=2,
        )
        image = _DEFAULT_IMAGE
    else:
        image = (
            getattr(getattr(framework_descriptor, "runtime", None), "image", None)
            or _DEFAULT_IMAGE
        )

    from tools.runners.docker_runner import DockerRunner

    runner = DockerRunner(image=image)

    def _run(test_file: Path, project_dir_arg: Path, seed: int):
        return runner.run_pytest(
            test_file=test_file,
            project_dir=project_dir_arg,
            seed=seed,
        )

    return _run


def _resolve_framework_descriptor(subtask):
    """Look up the FrameworkDescriptor for this subtask's ``framework`` field.

    For polyglot v0.2 subtasks (``subtask.framework`` is set) the descriptor
    is fetched from the framework registry and returned.

    For v0.1-style subtasks (``subtask.framework is None``) ``None`` is
    returned; the caller (``get_tfactory_gen_functional_prompt``) falls back
    to the legacy Python-specific prompt and emits a ``DeprecationWarning``.

    For subtasks whose ``framework`` value is present but unknown to the
    registry, a ``LookupError`` is raised with a helpful message listing the
    available framework names so the operator can diagnose the mismatch.

    Args:
        subtask: A Subtask dataclass or plain dict.

    Returns:
        A ``FrameworkDescriptor`` instance, or ``None`` for v0.1-style subtasks.

    Raises:
        LookupError: When ``subtask.framework`` is set but not registered.
    """
    if isinstance(subtask, dict):
        framework_name = subtask.get("framework")
    else:
        framework_name = getattr(subtask, "framework", None)

    if framework_name is None:
        # v0.1-style subtask — the prompt helper will warn and use the legacy path.
        return None

    try:
        from framework_registry import load_registry

        registry = load_registry()
        if framework_name not in registry:
            available = sorted(registry.keys())
            raise LookupError(
                f"gen_functional: subtask framework {framework_name!r} is not "
                f"registered in the framework registry. "
                f"Available frameworks: {available}. "
                "Check the frameworks/ directory or the subtask's framework field."
            )
        return registry[framework_name]
    except LookupError:
        raise
    except Exception as exc:
        # Registry unavailable (e.g. frameworks/ dir missing in a test env).
        # Log a warning and fall back to None so the legacy path fires rather
        # than crashing the whole gen_functional run.
        _gen_log.warning(
            "gen_functional: could not load framework registry for %r: %s — "
            "falling back to legacy prompt path",
            framework_name,
            exc,
        )
        return None


def _advance_to_evaluator(spec_dir: Path, project_dir: Path) -> None:
    """Advance to the verify (evaluate→triage) after gen_functional's success path.

    RFC-0016/0017 (#466) control/execution split: when ``TFACTORY_VERIFY_EXEC=kubejob``
    the verify is dispatched as a single k8s Job (running ``agents.verify_pipeline``
    on the nix-base image) instead of running in-pod. The split is fail-safe — if
    the Nix-lane sandbox isn't configured (no ``TFACTORY_NIX_RUNNER_IMAGE``) or the
    apply fails, ``dispatch_verify_job`` returns ``None`` and we fall back to the
    in-pod path so the verify is never stranded on a config/cluster gap. Default
    (unset / any other value) keeps today's in-pod ``schedule_evaluator`` behaviour.

    Lazy import — same defensive shape as _advance_to_planner_replan.
    Gated by ``TFACTORY_AUTO_EVALUATE`` (default ON; tests pin off).
    """
    if _dispatch_verify_as_job_if_enabled(spec_dir, project_dir):
        return
    try:
        from agents.evaluator import schedule_evaluator

        schedule_evaluator(spec_dir, project_dir, mode="initial")
    except ImportError as exc:
        _gen_log.warning(
            "could not auto-schedule evaluator: %s",
            exc,
        )


def _dispatch_verify_as_job_if_enabled(spec_dir: Path, project_dir: Path) -> bool:
    """Dispatch the verify as a k8s Job when the kubejob split is enabled.

    Returns ``True`` when the verify was dispatched as a Job (caller must NOT also
    run the in-pod path), or ``False`` when the in-pod path should run (mode is
    in-pod, or the kubejob dispatch fell back — fail-safe). Best-effort: any
    unexpected error falls back to in-pod rather than dropping the verify.

    The durable job-state id is the canonical ``$JOB_ID`` the control plane keys
    the row by (set by the web-server when it spawns this process); it falls back
    to the spec id for CLI runs that don't set it. The Job writes its own terminal
    row under this id, so the control plane reconciles by polling the same row.
    """
    try:
        from agents.verify_dispatch import (  # noqa: PLC0415 - lazy by design
            verify_exec_mode,
        )

        if verify_exec_mode() != "kubejob":
            return False

        status = _read_status(spec_dir)
        job_id = os.environ.get("JOB_ID") or spec_dir.name
        correlation_key = status.get("correlation_key") or status.get("issue_number")
        return _run_dispatch_blocking(job_id, spec_dir, project_dir, correlation_key)
    except Exception as exc:  # noqa: BLE001 — never drop the verify on a wiring error
        _gen_log.warning(
            "verify kubejob dispatch errored (%s); falling back to in-pod", exc
        )
        return False


def _run_dispatch_blocking(
    job_id: str,
    spec_dir: Path,
    project_dir: Path,
    correlation_key: object,
) -> bool:
    """Run dispatch_verify_job to completion and report whether it dispatched.

    Returns ``True`` only when a Job was actually dispatched (the verify is now
    running as a Job); ``False`` on fall-back (in-pod should run).
    """
    from agents.verify_dispatch import (  # noqa: PLC0415 - lazy by design
        dispatch_verify_job,
    )

    async def _go() -> bool:
        result = await dispatch_verify_job(
            job_id=job_id,
            spec_dir=spec_dir,
            project_dir=project_dir,
            correlation_key=correlation_key,  # type: ignore[arg-type]
        )
        if result is None:
            _gen_log.info(
                "verify kubejob dispatch fell back to in-pod for job_id=%s", job_id
            )
            return False
        _gen_log.info(
            "verify dispatched as k8s Job %s (job_id=%s)", result.job_name, job_id
        )
        return True

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        # Called from within the async pipeline: run the dispatch on a private
        # loop in a worker thread so we can return a sync decision without
        # re-entering the running loop.
        import concurrent.futures  # noqa: PLC0415 - lazy; only on the kubejob path

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(_go())).result()
    return asyncio.run(_go())


def _advance_to_review(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the opt-in review lane after gen_functional's success path.

    Best-effort + lazy import (same defensive shape as _advance_to_evaluator).
    No-op unless ``TFACTORY_REVIEW_LANE=1`` (the gate lives in schedule_review).
    The review lane is additive — it never blocks or feeds the verdict path.
    """
    try:
        from agents.review_lane import schedule_review

        schedule_review(spec_dir, project_dir, mode="initial")
    except Exception as exc:  # noqa: BLE001 — review is additive; never break gen
        _gen_log.warning("could not auto-schedule review lane: %s", exc)


# ─── The agent ──────────────────────────────────────────────────────────


def _collect_pending_subtasks(plan) -> list:
    """Return every PENDING subtask across all phases, regardless of lane.

    The per-subtask framework descriptor (Playwright / Jest / pytest / httpx)
    drives the prompt + runner image, so we no longer gate on Lane.UNIT —
    doing so silently dropped the browser/api/integration subtasks the Planner
    emitted (→ generated_empty).
    """
    from test_plan import SubtaskStatus

    return [
        st
        for phase in plan.phases
        for st in phase.subtasks
        if st.status == SubtaskStatus.PENDING
    ]


def _committed_test_subtasks(
    spec_dir: Path, plan: "ImplementationPlan"
) -> "list[Subtask]":
    """Subtasks Gen-Functional already committed a test file for in a prior run.

    A subtask counts as committed when it's COMPLETED *and* its first
    ``files_to_create`` path actually exists on disk. Used to decide whether a
    re-run that finds no PENDING subtasks (all remaining ones went STUCK) should
    still verify what exists rather than short-circuit to ``generated_empty``
    (#707): a couple of stuck subtasks must not zero out the whole spec's verify.
    """
    # lazy import: test_plan pulls SDK deps, kept out of module import
    from test_plan import SubtaskStatus  # noqa: PLC0415

    committed = []
    for phase in plan.phases:
        for st in phase.subtasks:
            if st.status != SubtaskStatus.COMPLETED:
                continue
            files = _files_to_create(st)
            if files and (spec_dir / files[0]).exists():
                committed.append(st)
    return committed


def _reject_subtask_for_replan(
    spec_dir: Path,
    project_dir: Path,
    plan,
    plan_file: Path,
    subtask,
    *,
    reason: str,
    phase: str,
    tests_generated: int,
    test_path: Path | None = None,
) -> bool:
    """Record a guardrail rejection and schedule a Planner replan.

    Deletes the offending test file (when one exists), writes
    ``context/replan_request.json``, persists the plan, flips status to
    ``replan_needed``, and forward-chains to the Planner. Always returns
    ``False`` so callers can ``return`` it directly to stop the loop.
    """
    if test_path is not None:
        test_path.unlink(missing_ok=True)
    _write_replan_request(
        spec_dir,
        subtask_id=subtask.id,
        reason=reason,
        failed_target=getattr(subtask, "target", "") or "",
    )
    # (A) #707: persist WHY on the subtask so it travels with the plan into
    # test_plan.json (the planner marks it stuck later; the reason rides along).
    subtask.replan_reason = reason
    plan.save(plan_file)
    # (A) #707: accumulate replan reasons in status.json so the failure is
    # visible there. The reasons were previously empty in status.json, which
    # made stuck subtasks invisible. read → append → write (write_status_patch
    # overwrites keys, so we carry the running list ourselves).
    replan_reasons = list(_read_status(spec_dir).get("replan_reasons") or [])
    replan_reasons.append(
        {"subtask_id": subtask.id, "phase": phase, "reason": reason, "at": _now_iso()}
    )
    _write_status_patch(
        spec_dir,
        status="replan_needed",
        phase=phase,
        last_rejected_subtask=subtask.id,
        # Persist the concrete rejection reason — #707 noted these were empty in
        # status.json, making stuck/replan loops impossible to diagnose after
        # the fact. replan_request.json is overwritten each replan; this keeps
        # the reason on the durable status record too. replan_reasons carries the
        # running accumulated list built just above.
        last_rejected_reason=reason,
        replan_reasons=replan_reasons,
        tests_generated=tests_generated,
    )
    _advance_to_planner_replan(spec_dir, project_dir)
    return False


async def _generate_one_subtask(
    spec_dir: Path,
    project_dir: Path,
    plan,
    plan_file: Path,
    subtask,
    tests_generated: int,
    verbose: bool,
) -> str:
    """Generate, guard, and record ONE subtask's test file.

    Returns one of:
      ``"generated"`` — file written and both guards passed (caller counts it)
      ``"skipped"``   — recoverable per-subtask failure; move to the next one
      ``"rejected"``  — a guardrail rejected and a Planner replan was scheduled;
                        the caller must stop the loop (return False)
    """
    from agents.flake_risk_lint import flake_risk_lint
    from agents.preflight_static import preflight_check
    from prompts_pkg.prompts import get_tfactory_gen_functional_prompt

    files = _files_to_create(subtask)
    if not files:
        subtask.fail("subtask had no files_to_create — Planner emit error")
        return "skipped"
    test_path = spec_dir / files[0]

    # Resolve the framework descriptor (v0.2 polyglot path). For v0.1-style
    # subtasks (framework=None) the descriptor is None, which triggers the
    # legacy prompt + DeprecationWarning.
    framework_descriptor = _resolve_framework_descriptor(subtask)

    prompt = get_tfactory_gen_functional_prompt(
        spec_dir,
        project_dir,
        subtask,
        framework_descriptor=framework_descriptor,
    )
    client = await _resolve_client(spec_dir, project_dir)
    session_status, _response, _err = await _invoke_session(
        client,
        prompt,
        spec_dir,
        verbose,
    )
    if session_status == "error":
        _gen_log.warning(
            "gen_functional: session error on subtask %s — skipping",
            subtask.id,
        )
        subtask.fail("SDK session returned status=error")
        return "skipped"

    # Did the agent actually write the file?
    if not test_path.exists():
        _reject_subtask_for_replan(
            spec_dir,
            project_dir,
            plan,
            plan_file,
            subtask,
            reason="agent did not Write the expected test file",
            phase="gen_functional_no_write",
            tests_generated=tests_generated,
        )
        return "rejected"

    source = test_path.read_text()

    # The pre-flight + flake-lint guards parse Python ASTs, so they only apply
    # to Python sources. For TS/JS (Playwright / Jest) and other languages they
    # would false-reject valid tests — skip them. language=None is the v0.1
    # legacy Python path, so treat it as Python.
    is_python = (subtask.language or "python") == "python"

    # Pre-flight static check (commit 2) — Python only.
    pre = preflight_check(source, project_dir=project_dir) if is_python else None
    if pre is not None and not pre.ok:
        reasons = (
            ", ".join(f"{f.describe()} — {f.reason[:80]}" for f in pre.failures)
            or pre.summary()
        )
        _reject_subtask_for_replan(
            spec_dir,
            project_dir,
            plan,
            plan_file,
            subtask,
            test_path=test_path,
            reason=f"pre-flight rejected: {reasons}",
            phase="gen_functional_preflight_rejected",
            tests_generated=tests_generated,
        )
        return "rejected"

    # Flake-risk lint (commit 3) — Python only (AST-based).
    flake = flake_risk_lint(source) if is_python else None
    if flake is not None and not flake.ok:
        reasons = (
            "; ".join(
                f"L{h.lineno} {h.pattern}: {h.detail[:60]}" for h in flake.rejected
            )
            or flake.summary()
        )
        _reject_subtask_for_replan(
            spec_dir,
            project_dir,
            plan,
            plan_file,
            subtask,
            test_path=test_path,
            reason=f"flake-lint rejected: {reasons}",
            phase="gen_functional_flake_rejected",
            tests_generated=tests_generated,
        )
        return "rejected"

    subtask.complete(output=f"wrote {files[0]}")
    return "generated"


def _maybe_scaffold_auth(spec_dir: Path, pending: list) -> None:
    """Scaffold the storageState login-once setup if any browser subtask needs auth.

    #107 (task 5): when a browser subtask authenticates against a ref-auth
    target, scaffold auth.setup.ts (logs in once) + a requires_auth Playwright
    config whose tests depend on the setup project + reuse its session.
    Best-effort: scaffolding must never fail the generation run.
    """
    needs_auth = any(
        getattr(st, "requires_auth", False)
        and "browser" in str(getattr(st, "lane", "")).lower()
        for st in pending
    )
    if not needs_auth:
        return
    try:
        from agents.evidence import scaffold_auth_setup

        scaffold_auth_setup(spec_dir)
    except Exception:  # noqa: BLE001 - never break generation on scaffolding
        pass


async def run_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Generate a test file for each pending subtask, across all lanes.

    Per-subtask loop (see ``_generate_one_subtask``):
      1. Build prompt via get_tfactory_gen_functional_prompt
      2. SDK session — the agent uses Write to emit ONE test file
      3. Pre-flight static check on the emitted source (commit 2 module)
      4. Flake-risk lint on the source (commit 3 module)
      5. If both pass → mark subtask completed, accumulate count
         If either rejects → delete file, write context/replan_request.json,
         schedule Planner replan, return False (stops the loop; next
         iteration handles whatever the replan emits)
      6. Session error → mark subtask failed, continue with the next

    Status transitions:
      pending/planned → generating → generated (with tests_generated count)
                                   → generated_empty (0 pending subtasks)
                                   → gen_functional_failed (hard error)
                                   → replan_needed (guardrail rejected;
                                                    Planner replan
                                                    auto-scheduled)
    """
    if not spec_dir.is_dir():
        _gen_log.error("gen_functional: spec_dir %s does not exist", spec_dir)
        return False

    try:
        _write_status_patch(
            spec_dir,
            status="generating",
            phase=f"gen_functional_{mode}_started",
        )

        from test_plan import ImplementationPlan

        plan_file = spec_dir / "test_plan.json"
        if not plan_file.exists():
            _write_status_patch(
                spec_dir,
                status="gen_functional_failed",
                phase="gen_functional_no_plan",
                gen_functional_error="test_plan.json missing — Planner didn't run?",
            )
            return False

        plan = ImplementationPlan.load(plan_file)
        pending = _collect_pending_subtasks(plan)
        if not pending:
            # (B) #707: no pending subtasks left, but earlier runs may have
            # committed real test files for subtasks that are now COMPLETED
            # (the rest went STUCK via the replan budget). Verify what we CAN
            # instead of emitting nothing — don't let a couple of stuck
            # subtasks zero out a spec whose other tests are ready to run.
            committed = _committed_test_subtasks(spec_dir, plan)
            if committed:
                _write_status_patch(
                    spec_dir,
                    status="generated",
                    phase="gen_functional_partial_verify",
                    tests_generated=len(committed),
                    gen_functional_warnings=[
                        f"partial plan: verifying {len(committed)} committed "
                        "test(s); remaining subtasks are stuck (see "
                        "replan_reasons)"
                    ],
                )
                _advance_to_evaluator(spec_dir, project_dir)
                _advance_to_review(spec_dir, project_dir)
                return True
            _write_status_patch(
                spec_dir,
                status="generated_empty",
                phase="gen_functional_no_pending",
                tests_generated=0,
                gen_functional_warnings=["no pending subtasks to generate"],
            )
            return True

        tests_generated = 0
        total = len(pending)
        for idx, subtask in enumerate(pending, start=1):
            # Heartbeat before each subtask: a multi-subtask generation can run
            # many minutes, and `_generate_one_subtask` writes no status of its
            # own, so without this the spec's `updated_at` freezes for the whole
            # loop and the #95 liveness watchdog false-stalls a healthy run. One
            # patch per subtask keeps `updated_at` fresh so a stall verdict means
            # the process is genuinely gone, not just busy (#742/#774).
            _write_status_patch(
                spec_dir,
                status="generating",
                phase=f"gen_functional_subtask_{idx}_of_{total}",
            )
            outcome = await _generate_one_subtask(
                spec_dir,
                project_dir,
                plan,
                plan_file,
                subtask,
                tests_generated,
                verbose,
            )
            if outcome == "rejected":
                return False
            if outcome == "generated":
                tests_generated += 1

        plan.save(plan_file)

        if tests_generated == 0:
            _write_status_patch(
                spec_dir,
                status="gen_functional_failed",
                phase="gen_functional_no_tests_generated",
                tests_generated=0,
                gen_functional_error=(
                    "every pending subtask failed (session errors); no "
                    "tests generated and no replan request written"
                ),
            )
            return False

        # Assertion pinning (#283): on a handback re-run the suite is
        # regenerated from scratch. If a manifest was pinned on the first
        # failure, the regenerated suite may only *add* assertions — a dropped
        # or loosened one means the verification bar moved, which can mask the
        # very bug the handback is meant to fix. Reject the cycle rather than let
        # a weakened suite advance to the Evaluator and pass. No-op on first
        # runs (nothing pinned → the gate is off), so normal generation is
        # unaffected. Best-effort: a guard error never blocks a healthy suite.
        try:
            from agents.handback.assertion_manifest import check_drift

            drift = check_drift(spec_dir, spec_dir / "tests")
        except Exception:  # noqa: BLE001 — never block generation on the guard
            drift = None
        if drift is not None and not drift.ok:
            (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
            (spec_dir / "findings" / "assertion_drift.json").write_text(
                json.dumps(drift.to_dict(), indent=2)
            )
            _gen_log.warning(
                "gen_functional: assertion drift REJECTED — %d pinned test "
                "file(s) weakened/removed on re-generation: %s",
                len(drift.violations),
                [v.path for v in drift.violations],
            )
            _write_status_patch(
                spec_dir,
                status="gen_functional_failed",
                phase="assertion_drift_rejected",
                tests_generated=tests_generated,
                gen_functional_error=(
                    "regenerated suite weakened pinned assertions in "
                    f"{len(drift.violations)} file(s); rejected to preserve the "
                    "verification bar (#283)"
                ),
            )
            return False

        _maybe_scaffold_auth(spec_dir, pending)

        _write_status_patch(
            spec_dir,
            status="generated",
            phase="gen_functional_complete",
            tests_generated=tests_generated,
        )
        # Forward-chain to the Evaluator (Task 7, #8 — commit 1 lands the
        # scheduler + stub). Gated by ``TFACTORY_AUTO_EVALUATE`` env;
        # tests pin it off to keep this layer deterministic.
        _advance_to_evaluator(spec_dir, project_dir)
        # Opt-in review lane (additive, parallel, never feeds the verdict path).
        # Gated by ``TFACTORY_REVIEW_LANE`` (default OFF).
        _advance_to_review(spec_dir, project_dir)
        return True

    except Exception as exc:
        _gen_log.error("gen_functional failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="gen_functional_failed",
            phase=f"gen_functional_{mode}_exception",
            gen_functional_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as planner's _BG_PLANNER_TASKS. The planner
# success paths call schedule_gen_functional after writing
# status=planned / planned_empty; gating on env keeps tests
# deterministic.

_BG_GEN_FUNCTIONAL_TASKS: set[asyncio.Task] = set()


def schedule_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Gen-Functional, gated by ``TFACTORY_AUTO_GENERATE``.

    Default off in test fixtures (set ``TFACTORY_AUTO_GENERATE=0``);
    production sets ``=1`` so the pipeline auto-advances from Planner
    to Gen-Functional with no manual step.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-generation. Each scheduled task is anchored in
    ``_BG_GEN_FUNCTIONAL_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("TFACTORY_AUTO_GENERATE", "1") == "0":
        return None
    task = asyncio.create_task(run_gen_functional(spec_dir, project_dir, mode=mode))
    _BG_GEN_FUNCTIONAL_TASKS.add(task)
    task.add_done_callback(_BG_GEN_FUNCTIONAL_TASKS.discard)
    return task

"""
QA Validation Loop
==================

Restores the top-level ``qa_loop`` module imported by the build CLI
(``cli/qa_commands.py`` and ``cli/build_commands.py``). The module was
never committed (see #226 / #227), so the agent CLI crashed at import
time with ``ModuleNotFoundError: No module named 'qa_loop'`` and every
task failed in the planning phase.

Public API (the four symbols the call sites import):

- ``print_qa_status(spec_dir)``   — render the current QA sign-off
- ``should_run_qa(spec_dir)``     — build complete and not yet approved
- ``is_qa_approved(spec_dir)``    — QA sign-off status == "approved"
- ``run_qa_validation_loop(...)`` — async reviewer→fixer loop, returns approved

QA state lives in ``test_plan.json["qa_signoff"]`` — the exact schema the
``update_qa_status`` SDK tool writes (see ``agents/tools_pkg/tools/qa.py``).
This module never writes that schema itself; it orchestrates the QA agents
that do, then reads the result.

Heavy imports (SDK client, prompts, providers) are deferred into the
functions that need them so ``import qa_loop`` stays bulletproof — that is
exactly the property the CI import-guard (#227) protects.
"""

from __future__ import annotations

import json
from pathlib import Path

# Bounded number of reviewer→fixer rounds before giving up and handing
# off to human review. Keeps the loop from spinning forever on an issue
# the fixer can't resolve ("no infinite agent loops" policy).
QA_MAX_ITERATIONS = 3


# ─── QA state readers (pure, stdlib-only) ───────────────────────────────


def _load_plan(spec_dir: Path) -> dict:
    """Load ``test_plan.json``; return ``{}`` if absent or unreadable."""
    plan_file = Path(spec_dir) / "test_plan.json"
    if not plan_file.exists():
        return {}
    try:
        with open(plan_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _qa_signoff(spec_dir: Path) -> dict:
    """Return the ``qa_signoff`` block from the plan (``{}`` if none)."""
    return _load_plan(spec_dir).get("qa_signoff", {}) or {}


def is_qa_approved(spec_dir: Path) -> bool:
    """True when QA has signed off (``qa_signoff.status == "approved"``)."""
    return _qa_signoff(spec_dir).get("status") == "approved"


def should_run_qa(spec_dir: Path) -> bool:
    """
    True when the build is finished but QA has not yet approved it.

    A ``rejected`` / ``fixes_applied`` sign-off still returns True so the
    loop re-validates; an ``approved`` sign-off returns False.
    """
    # Deferred import keeps module load cheap and crash-proof.
    from core.progress import is_build_complete

    return is_build_complete(spec_dir) and not is_qa_approved(spec_dir)


def print_qa_status(spec_dir: Path) -> None:
    """Render the current QA sign-off status to stdout."""
    from ui import Icons, icon, info, success, warning

    signoff = _qa_signoff(spec_dir)
    if not signoff:
        print(info(f"{icon(Icons.INFO)} No QA sign-off recorded yet."))
        return

    status = signoff.get("status", "unknown")
    session = signoff.get("qa_session", 0)
    issues = signoff.get("issues_found", []) or []
    timestamp = signoff.get("timestamp", "")

    if status == "approved":
        print(success(f"{icon(Icons.SUCCESS)} QA approved"))
    elif status == "rejected":
        print(warning(f"{icon(Icons.WARNING)} QA rejected — {len(issues)} issue(s)"))
    else:
        print(info(f"{icon(Icons.INFO)} QA status: {status}"))

    print(f"  Session:   {session}")
    if issues:
        print(f"  Issues:    {len(issues)}")
    if timestamp:
        print(f"  Updated:   {timestamp}")


# ─── reviewer→fixer orchestration ───────────────────────────────────────


def _resolve_qa_client(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    agent_type: str,
    phase: str,
):
    """
    Build an SDK client / provider for a QA agent.

    Mirrors ``evaluator._resolve_evaluator_client``: Claude models go
    through ``create_client`` (with the QA ``agent_type`` so the right
    tools + MCP servers are wired); other providers go through the
    provider factory. Patchable seam — tests mock this without the SDK.
    """
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    qa_model = get_phase_model(spec_dir, phase, model)
    provider_name = infer_provider_from_model(qa_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, phase)
        return create_client(
            project_dir,
            spec_dir,
            qa_model,
            max_thinking_tokens=thinking_budget,
            agent_type=agent_type,
        )
    extra = get_provider_extra_kwargs(provider_name, qa_model)
    # Ollama runs file ops through TFactory's sandboxed ToolExecutor; the
    # QA agents read/write inside the spec dir, so allow it explicitly.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase=phase,
        working_dir=project_dir,
        model=extra.pop("model", qa_model),
        **extra,
    )


async def _invoke_qa_session(client, prompt: str, spec_dir: Path, verbose: bool):
    """Run one QA agent session. Patchable seam (cf. ``evaluator._invoke_session``)."""
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client,
            prompt,
            spec_dir,
            verbose,
            phase=LogPhase.VALIDATION,
        )


async def _run_qa_reviewer(
    project_dir: Path, spec_dir: Path, model: str, verbose: bool
):
    """Run the qa_reviewer agent; it records its verdict via update_qa_status."""
    from prompts_pkg import get_qa_reviewer_prompt

    client = _resolve_qa_client(project_dir, spec_dir, model, "qa_reviewer", "qa")
    prompt = get_qa_reviewer_prompt(spec_dir, project_dir)
    return await _invoke_qa_session(client, prompt, spec_dir, verbose)


async def _run_qa_fixer(project_dir: Path, spec_dir: Path, model: str, verbose: bool):
    """Run the qa_fixer agent to address issues the reviewer flagged."""
    from prompts_pkg import get_qa_fixer_prompt

    client = _resolve_qa_client(project_dir, spec_dir, model, "qa_fixer", "qa_fixer")
    prompt = get_qa_fixer_prompt(spec_dir, project_dir)
    return await _invoke_qa_session(client, prompt, spec_dir, verbose)


async def run_qa_validation_loop(
    *,
    project_dir: Path,
    spec_dir: Path,
    model: str,
    verbose: bool = False,
) -> bool:
    """
    Drive a bounded QA reviewer→fixer loop and return whether QA approved.

    Each round runs the qa_reviewer agent (which records its verdict in
    ``test_plan.json["qa_signoff"]`` via the ``update_qa_status`` tool). On
    approval the loop returns ``True`` immediately. On rejection it runs the
    qa_fixer agent and re-reviews, up to ``QA_MAX_ITERATIONS`` rounds, after
    which it returns the final approval state (``False`` → human review).

    Keyword-only to match the call sites in ``cli/qa_commands.py`` and
    ``cli/build_commands.py``.
    """
    project_dir = Path(project_dir)
    spec_dir = Path(spec_dir)

    for round_num in range(1, QA_MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n── QA round {round_num}/{QA_MAX_ITERATIONS} ──")

        status, _response, err = await _run_qa_reviewer(
            project_dir, spec_dir, model, verbose
        )
        if status == "error":
            print(f"\n⚠️  QA reviewer error: {err.get('message', 'unknown error')}")
            return is_qa_approved(spec_dir)

        if is_qa_approved(spec_dir):
            return True

        # Not approved — run the fixer, unless this was the final round.
        if round_num == QA_MAX_ITERATIONS:
            break

        if verbose:
            print("  QA found issues — running fixer…")
        fix_status, _fix_response, fix_err = await _run_qa_fixer(
            project_dir, spec_dir, model, verbose
        )
        if fix_status == "error":
            print(f"\n⚠️  QA fixer error: {fix_err.get('message', 'unknown error')}")
            return is_qa_approved(spec_dir)

    return is_qa_approved(spec_dir)

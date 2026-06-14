"""TFactory review lane — runs an LLM "staff engineer" reviewer over the build's
code and writes findings to ``findings/review.json``.

A complementary verify signal (alongside unit/api/mutation), opt-in and additive:
it does NOT touch the evaluator/triager/verdict contract. Reuses the same SDK +
session plumbing as the generator (``gen_functional``). The persona prompt is
``prompts/review_lane.md`` (adapted from the vendored ``code-reviewer`` agent).

Entry point: ``run_review_lane(spec_dir, project_dir, mode, verbose) -> bool``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_status(spec_dir: Path) -> dict:
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Best-effort push-based progress event; no-op unless opted in.
    try:
        from agents.stage_events import emit_stage_event

        emit_stage_event(spec_dir, status, stage="review")
    except Exception:  # noqa: BLE001 — progress events must never break the lane
        _log.debug("review stage event emit failed (best-effort)", exc_info=True)


def _build_prompt(spec_dir: Path, project_dir: Path) -> str:
    """Context header + the review persona body."""
    body = (_PROMPTS_DIR / "review_lane.md").read_text()
    header = (
        "## REVIEW CONTEXT\n\n"
        f"- Spec/workspace dir (write `findings/review.json` here): `{spec_dir}`\n"
        f"- Project under test (read-only, review the source here): `{project_dir}`\n"
        "- Review only what the change touches. Write the JSON contract below.\n\n"
        "---\n\n"
    )
    return header + body


# ─── SDK seams (mockable in tests) ──────────────────────────────────────────


async def _invoke_session(client, prompt: str, spec_dir: Path, verbose: bool):
    """Wrap run_agent_session so tests can patch one symbol."""
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client, prompt, spec_dir, verbose, phase=LogPhase.CODING,
        )


def _findings_count(spec_dir: Path) -> int | None:
    """Read findings/review.json the LLM wrote; None if absent/unparseable."""
    fp = spec_dir / "findings" / "review.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return len(data.get("findings") or [])


async def run_review_lane(
    spec_dir: Path,
    project_dir: Path,
    mode: str = "initial",
    verbose: bool = False,
) -> bool:
    """Run the reviewer persona over the build. Returns True on a completed review.

    Best-effort and additive: any failure is recorded on status and returns False,
    never raising into the verify orchestration.
    """
    spec_dir = Path(spec_dir)
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    _write_status_patch(spec_dir, phase=f"review_{mode}_started", status="reviewing")

    try:
        from agents.gen_functional import _resolve_client  # reuse the proven seam

        client = await _resolve_client(spec_dir, project_dir)
        await _invoke_session(client, _build_prompt(spec_dir, project_dir), spec_dir, verbose)
    except Exception as exc:  # noqa: BLE001 — never break verify
        _log.warning("review lane failed: %s", exc)
        _write_status_patch(
            spec_dir, phase=f"review_{mode}_failed", status="review_failed",
            review_error=str(exc)[:300],
        )
        return False

    count = _findings_count(spec_dir)
    if count is None:
        # The contract requires the reviewer to write findings/review.json.
        _write_status_patch(
            spec_dir, phase=f"review_{mode}_failed", status="review_failed",
            review_error="no_evidence: reviewer wrote no findings/review.json",
        )
        return False

    _write_status_patch(
        spec_dir, phase=f"review_{mode}_complete", status="reviewed",
        review_findings_count=count,
    )
    return True

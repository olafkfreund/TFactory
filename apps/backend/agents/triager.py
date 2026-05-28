"""Triager agent — Task 8, issue #9.

Final agent in the six-agent TFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads ``findings/verdicts.json`` (written by the Evaluator), wraps
each entry as a TriageCandidate, filters out rejects, dedups +
ranks via the commit-2 primitives, renders triage_report.{md,json}
via commit-3's renderer, then invokes the commit-4 git_writer +
pr_comment helpers (both DRY-RUN by default for safety — production
opts in via env flags).

Per CLAUDE.md: "NO automatic pushes to GitHub - user controls when
to push". Real-run defaults are off; the operator flips them per
deployment.

Task 8 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Dedup + rank primitives
  ✓ commit 3 — Triage report rendering (golden-file snapshot)
  ✓ commit 4 — git_writer + pr_comment helpers (dry-run first)
  ✓ commit 5 — Real run_triager wires everything
  ✓ commit 6 — Integration test + close #9

  (Sub-task 8.4 — trim AIFactory's runners/github/ to a minimal
  pr_comment.py — is deferred to a follow-up commit; the web-server
  still consumes pieces of that tree and needs careful surgery.)
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


_triage_log = _logging.getLogger(__name__)


# ─── Workspace helpers ─────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# ─── Mode resolution: dry-run vs real ─────────────────────────────────


def _truthy(env_val: str | None) -> bool:
    if env_val is None:
        return False
    return env_val.strip().lower() in ("1", "true", "yes", "on")


def _git_writer_dry_run() -> bool:
    """Default ON (dry). Operator sets TFACTORY_TRIAGER_GIT_WRITE=1 to
    actually commit to the AIFactory branch."""
    return not _truthy(os.environ.get("TFACTORY_TRIAGER_GIT_WRITE"))


def _pr_comment_dry_run() -> bool:
    """Default ON (dry). Operator sets TFACTORY_TRIAGER_PR_COMMENT=1 to
    actually post via gh pr comment."""
    return not _truthy(os.environ.get("TFACTORY_TRIAGER_PR_COMMENT"))


# ─── The agent itself ───────────────────────────────────────────────────


async def run_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the TFactory Triager agent.

    Args:
        spec_dir: TFactory workspace spec directory.
        project_dir: AIFactory project root_path (read; used as the
            git_writer's repo_dir when committing).
        mode: 'initial' on first run; 'rerun' for retriggers. Surfaced
            in the report.
        verbose: forwarded for log-level routing in future commits.

    Returns:
        True on clean completion (including dry-run + empty cases);
        False on hard failure.

    Status transitions:
      evaluated   → triaging          (in-flight)
                  → triaged           (report rendered + side-effects done)
                  → triaged_empty     (no verdicts to act on)
                  → triager_failed    (any hard error)
    """
    del verbose
    try:
        _write_status_patch(
            spec_dir,
            status="triaging",
            phase=f"triager_{mode}_started",
        )

        # Lazy imports — keeps test mocking simple + avoids loading
        # heavy modules when the path isn't taken (e.g., evaluator
        # failures land here pre-load).
        from agents.triage_dedup import (
            TriageCandidate,
            dedup_candidates,
            rank_candidates,
        )
        from agents.triage_report import build_report, render_json, render_markdown
        from tools.git_writer import GitWriteRequest, write_tests_to_branch
        from tools.pr_comment import PRCommentRequest, post_pr_comment

        # ── 1. Load verdicts.json ────────────────────────────────
        verdicts_path = spec_dir / "findings" / "verdicts.json"
        if not verdicts_path.exists():
            _write_status_patch(
                spec_dir,
                status="triager_failed",
                phase="triager_no_verdicts",
                triager_error="findings/verdicts.json not found",
            )
            return False

        try:
            doc = json.loads(verdicts_path.read_text())
        except json.JSONDecodeError as exc:
            _write_status_patch(
                spec_dir,
                status="triager_failed",
                phase="triager_verdicts_unparseable",
                triager_error=f"verdicts.json invalid: {exc}",
            )
            return False

        verdicts = doc.get("verdicts") or []

        # ── 2. Wrap as TriageCandidates ─────────────────────────
        candidates: list[TriageCandidate] = []
        for v in verdicts:
            tid = v.get("test_id")
            test_file = v.get("test_file")
            if not tid or not test_file:
                # Skip malformed entries — the Evaluator's validator
                # should have caught them, but be defensive
                continue
            test_path = spec_dir / test_file
            source = ""
            if test_path.exists():
                try:
                    source = test_path.read_text(encoding="utf-8")
                except OSError:
                    source = ""
            candidates.append(TriageCandidate(
                test_id=tid,
                test_file=test_file,
                verdict=v,
                source=source,
            ))

        # ── 3. Bucket by verdict + dedup the keepers ────────────
        keepers = [c for c in candidates if c.verdict_label in ("accept", "flag")]
        rejects = [c for c in candidates if c.verdict_label == "reject"]

        if not candidates:
            # No verdicts at all — empty pass.
            _write_empty_report(spec_dir, mode)
            _write_status_patch(
                spec_dir,
                status="triaged_empty",
                phase="triager_no_candidates",
                committed_count=0,
                rejected_count=0,
                flagged_count=0,
                dedup_collision_count=0,
            )
            return True

        dedup_result = dedup_candidates(keepers)
        ranked_survivors = rank_candidates(dedup_result.kept)
        # Re-bucket the ranked survivors by verdict label
        committed = tuple(c for c in ranked_survivors if c.verdict_label == "accept")
        flagged = tuple(c for c in ranked_survivors if c.verdict_label == "flag")

        # ── 4. Build + render the report ────────────────────────
        report = build_report(
            mode=mode,
            generated_at=_now_iso(),
            committed=committed,
            flagged=flagged,
            rejected=tuple(rejects),
            collisions=dedup_result.collisions,
            dedup_input_count=len(keepers),
        )

        findings_dir = spec_dir / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / "triage_report.json").write_text(render_json(report))
        report_md = render_markdown(report)
        (findings_dir / "triage_report.md").write_text(report_md)

        # ── 5. git_writer side-effect (dry-run by default) ──────
        git_dry = _git_writer_dry_run()
        git_result_summary: dict = {"skipped": True, "reason": "no side-effect path"}
        source_meta = _load_source_meta(spec_dir)
        branch = source_meta.get("branch") or ""
        if committed or flagged:
            files_to_commit = tuple(
                (c.test_file, c.source) for c in (*committed, *flagged)
                if c.source  # only commit files we managed to read
            )
            if branch and files_to_commit:
                request = GitWriteRequest(
                    repo_dir=project_dir,
                    branch=branch,
                    files=files_to_commit,
                    commit_msg=(
                        f"tfactory: add {len(committed)} accepted "
                        f"+ {len(flagged)} flagged tests"
                    ),
                )
                gw = write_tests_to_branch(request, dry_run=git_dry)
                git_result_summary = {
                    "skipped": False,
                    "dry_run": gw.dry_run,
                    "ok": gw.ok,
                    "committed_paths": list(gw.committed_paths),
                    "commit_sha": gw.commit_sha,
                    "error": gw.error,
                    "argv_log": [list(a) for a in gw.argv_log],
                }
            else:
                git_result_summary = {
                    "skipped": True,
                    "reason": (
                        "no branch in source.json" if not branch
                        else "no readable test sources"
                    ),
                }

        # ── 6. pr_comment side-effect (dry-run by default) ──────
        pr_dry = _pr_comment_dry_run()
        pr_number = int(source_meta.get("pr_number") or 0)
        pr_comment_summary: dict = {"skipped": True, "reason": "no PR number"}
        if pr_number > 0 and report_md:
            request = PRCommentRequest(
                repo_dir=project_dir,
                pr_number=pr_number,
                body=report_md,
                repo_slug=source_meta.get("repo_slug") or None,
            )
            pc = post_pr_comment(request, dry_run=pr_dry)
            pr_comment_summary = {
                "skipped": False,
                "dry_run": pc.dry_run,
                "ok": pc.ok,
                "argv": list(pc.argv),
                "body_bytes": pc.body_bytes,
                "comment_url": pc.comment_url,
                "error": pc.error,
            }
        else:
            # No PR number — write the comment body to disk so the
            # operator can paste it manually.
            (findings_dir / "pr_comment_body.md").write_text(report_md)
            pr_comment_summary = {
                "skipped": True,
                "reason": "no PR number in source.json",
                "body_written_to": str(findings_dir / "pr_comment_body.md"),
            }

        # ── 7. Record summaries in status.json ──────────────────
        committed_count = len(committed)
        flagged_count = len(flagged)
        rejected_count = len(rejects)
        collision_count = len(dedup_result.collisions)
        final_status = "triaged" if (committed_count or flagged_count) else "triaged_empty"
        _write_status_patch(
            spec_dir,
            status=final_status,
            phase="triager_complete",
            committed_count=committed_count,
            rejected_count=rejected_count,
            flagged_count=flagged_count,
            dedup_collision_count=collision_count,
            git_writer=git_result_summary,
            pr_comment=pr_comment_summary,
        )
        return True

    except Exception as exc:
        _triage_log.error(
            "triager failed: %s\n%s", exc, traceback.format_exc()
        )
        _write_status_patch(
            spec_dir,
            status="triager_failed",
            phase=f"triager_{mode}_exception",
            triager_error=str(exc)[:500],
        )
        return False


def _load_source_meta(spec_dir: Path) -> dict:
    """Load context/source.json. Returns {} on absence or parse failure."""
    p = spec_dir / "context" / "source.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_empty_report(spec_dir: Path, mode: str) -> None:
    """Write empty placeholder reports for the no-candidates path."""
    from agents.triage_report import build_report, render_json, render_markdown
    empty_report = build_report(
        mode=mode,
        generated_at=_now_iso(),
        committed=[],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=0,
    )
    findings_dir = spec_dir / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    (findings_dir / "triage_report.json").write_text(render_json(empty_report))
    (findings_dir / "triage_report.md").write_text(render_markdown(empty_report))


# ─── Auto-fire scheduler ─────────────────────────────────────────────────

_BG_TRIAGER_TASKS: set[asyncio.Task] = set()


def schedule_triager(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Triager, gated by ``TFACTORY_AUTO_TRIAGE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``TFACTORY_AUTO_TRIAGE=0`` to keep evaluator's success path from
    auto-advancing.
    """
    if os.environ.get("TFACTORY_AUTO_TRIAGE", "1") == "0":
        return None
    task = asyncio.create_task(
        run_triager(spec_dir, project_dir, mode=mode)
    )
    _BG_TRIAGER_TASKS.add(task)
    task.add_done_callback(_BG_TRIAGER_TASKS.discard)
    return task

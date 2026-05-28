"""Snapshot an AIFactory spec into a TFactory workspace — Task 3 (#4).

Extended in Task 4 (#20) to also capture ``.tfactory.yml`` and
``.tfactory/tests-catalog.json`` from the AIFactory project root so the
Planner agent sees them in ``context/`` alongside the spec and diff.

Why a snapshot rather than reading the AIFactory spec live each time:

- The AIFactory spec dir may be mutated (next task, follow-up planner)
  while the TFactory pipeline is running. Snapshotting at handover time
  gives the test pipeline a stable frozen contract.
- AIFactory's spec schema may change (new fields, renamed files) — we
  snapshot at the version that handover saw, and apply a schema check
  inside our copy. The upstream change won't break in-flight tests.
- The snapshot is mode 0o444 so any accidental write blows up loudly
  rather than silently corrupting the test pipeline's input.

What gets snapshotted, in
``~/.tfactory/workspaces/{project_id}/specs/{spec_id}/context/``:

  source.json           — handover metadata (paths, refs, sha, timestamps)
  aifactory_spec.md     — copy of AIFactory's spec.md (0o444)
  aifactory_plan.json   — copy of AIFactory's implementation_plan.json
                          (0o444; absent if upstream doesn't have one)
  diff.patch            — ``git diff base_ref..branch`` from the project's
                          root_path (absent if git unavailable / refs missing)
  tfactory_yml.json     — parsed ``.tfactory.yml`` as JSON (0o444; absent if
                          the file doesn't exist or fails validation)
  tests_catalog.json    — copy of ``.tfactory/tests-catalog.json`` as JSON
                          (0o444; absent if the file doesn't exist or is invalid)

The snapshotter is intentionally tolerant of partial sources — if the
AIFactory spec dir has spec.md but no implementation_plan.json, we
snapshot what's there and note the gap in source.json. Callers can
gate further pipeline steps on those gaps; the snapshotter doesn't.
Both v0.2 files (``.tfactory.yml`` and the tests-catalog) are OPTIONAL —
most v0.2 repos will adopt them gradually; absence is a recorded fact,
not an error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants + helpers
# ---------------------------------------------------------------------------

_DEFAULT_AIFACTORY_ROOT = Path.home() / ".aifactory"
_SNAPSHOT_MODE = 0o444  # read-only


def _aifactory_root() -> Path:
    """Resolve AIFactory's workspace root. Env override for tests."""
    root = os.environ.get("TFACTORY_AIFACTORY_ROOT")
    return Path(root).expanduser() if root else _DEFAULT_AIFACTORY_ROOT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------


class SnapshotError(Exception):
    """Raised when a snapshot fails at a contract boundary (missing source)."""


@dataclass
class SnapshotResult:
    """Outcome of a snapshot operation. Always serialisable to source.json."""

    project_id: str
    spec_id: str
    branch: str
    base_ref: str
    aifactory_spec_dir: str
    snapshotted_at: str = field(default_factory=_now_iso)

    # What we actually captured. False = source file missing / git failed.
    has_spec_md: bool = False
    has_plan_json: bool = False
    has_diff_patch: bool = False
    has_tfactory_yml: bool = False
    has_tests_catalog: bool = False

    # Git context at handover time.
    sha_at_handover: str | None = None
    diff_stat: str | None = None  # e.g. "3 files changed, +12 -4"

    # Warnings — soft failures that callers may surface to the user.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Core snapshot routine
# ---------------------------------------------------------------------------


def snapshot_aifactory_spec(
    *,
    project_id: str,
    spec_id: str,
    branch: str,
    base_ref: str,
    project_root_path: Path | str | None,
    dest_spec_dir: Path | str,
    aifactory_root: Path | None = None,
) -> SnapshotResult:
    """Snapshot an AIFactory spec into a TFactory workspace context dir.

    Args:
        project_id: AIFactory project ID.
        spec_id: AIFactory spec ID.
        branch: Git branch carrying the completed feature.
        base_ref: Git ref the branch was forked from (typically ``main``).
        project_root_path: Local path to the project checkout where
            git commands run. Pass None to skip git operations
            (diff/sha will not be captured; warning emitted).
        dest_spec_dir: TFactory workspace spec dir to write the
            ``context/`` subtree into.
        aifactory_root: Optional override for ``~/.aifactory``. Useful
            in tests; production code uses the default.

    Returns:
        A SnapshotResult describing what landed. Always also written
        to ``dest_spec_dir/context/source.json``.

    Raises:
        SnapshotError: if the AIFactory spec dir itself is missing.
            Per-file misses are recorded as warnings instead.
    """
    root = aifactory_root if aifactory_root is not None else _aifactory_root()
    source_dir = root / "workspaces" / project_id / "specs" / spec_id

    if not source_dir.is_dir():
        raise SnapshotError(
            f"AIFactory spec dir not found: {source_dir}. "
            "Check project_id/spec_id and that the AIFactory workspace "
            "is on the same host as TFactory."
        )

    dest_spec = Path(dest_spec_dir)
    context_dir = dest_spec / "context"
    context_dir.mkdir(parents=True, exist_ok=True)

    result = SnapshotResult(
        project_id=project_id,
        spec_id=spec_id,
        branch=branch,
        base_ref=base_ref,
        aifactory_spec_dir=str(source_dir),
    )

    # ── spec.md → aifactory_spec.md (0o444) ──────────────────────────────
    src_spec = source_dir / "spec.md"
    if src_spec.is_file():
        dst_spec = context_dir / "aifactory_spec.md"
        shutil.copyfile(src_spec, dst_spec)
        dst_spec.chmod(_SNAPSHOT_MODE)
        result.has_spec_md = True
    else:
        result.warnings.append(f"spec.md missing in {source_dir}")

    # ── implementation_plan.json → aifactory_plan.json (0o444) ───────────
    src_plan = source_dir / "implementation_plan.json"
    if src_plan.is_file():
        dst_plan = context_dir / "aifactory_plan.json"
        shutil.copyfile(src_plan, dst_plan)
        dst_plan.chmod(_SNAPSHOT_MODE)
        result.has_plan_json = True
    else:
        result.warnings.append(f"implementation_plan.json missing in {source_dir}")

    # ── Git diff + sha capture (optional) ────────────────────────────────
    if project_root_path is not None:
        repo = Path(project_root_path).expanduser()
        if repo.is_dir():
            sha = _git_rev_parse(repo, branch)
            if sha is not None:
                result.sha_at_handover = sha
            else:
                result.warnings.append(f"could not resolve branch {branch!r} in {repo}")
            diff_text, diff_stat, diff_err = _git_diff(repo, base_ref, branch)
            if diff_text is not None:
                diff_path = context_dir / "diff.patch"
                diff_path.write_text(diff_text)
                result.has_diff_patch = True
                result.diff_stat = diff_stat
            else:
                result.warnings.append(diff_err or "git diff failed (unknown reason)")
        else:
            result.warnings.append(
                f"project_root_path {repo} not a directory; git context skipped"
            )
    else:
        result.warnings.append("project_root_path not provided; git context skipped")

    # ── .tfactory.yml → context/tfactory_yml.json (if present) ──────────
    if project_root_path is not None:
        repo = Path(project_root_path).expanduser()
        if repo.is_dir():
            from tfactory_yml import TFactoryYmlError, load_tfactory_yml

            cfg = None
            try:
                cfg = load_tfactory_yml(repo)
            except TFactoryYmlError as exc:
                result.warnings.append(
                    f".tfactory.yml present but unparseable: {exc.message}"
                )
                cfg = None
            if cfg is not None:
                dst = context_dir / "tfactory_yml.json"
                dst.write_text(
                    json.dumps(cfg.model_dump(mode="json"), indent=2, sort_keys=True)
                )
                dst.chmod(_SNAPSHOT_MODE)
                result.has_tfactory_yml = True

    # ── .tfactory/tests-catalog.json → context/tests_catalog.json ────────
    if project_root_path is not None:
        repo = Path(project_root_path).expanduser()
        if repo.is_dir():
            from tests_catalog import CatalogError, load_catalog

            cat = None
            try:
                cat = load_catalog(repo)
            except CatalogError as exc:
                result.warnings.append(
                    f"tests-catalog.json present but unparseable: {exc.reason}"
                )
                cat = None
            if cat is not None:
                dst = context_dir / "tests_catalog.json"
                dst.write_text(json.dumps(cat.to_dict(), indent=2, sort_keys=True))
                dst.chmod(_SNAPSHOT_MODE)
                result.has_tests_catalog = True

    # ── source.json ──────────────────────────────────────────────────────
    (context_dir / "source.json").write_text(json.dumps(result.to_dict(), indent=2))

    return result


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_rev_parse(repo: Path, ref: str) -> str | None:
    """Return the full sha for ``ref`` inside ``repo``, or None on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _git_diff(
    repo: Path, base_ref: str, branch: str
) -> tuple[str | None, str | None, str | None]:
    """Return (patch_text, stat_summary, error_message). Any may be None."""
    try:
        diff_proc = subprocess.run(
            ["git", "-C", str(repo), "diff", f"{base_ref}..{branch}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, None, f"git not available or timed out: {exc}"

    if diff_proc.returncode != 0:
        return (
            None,
            None,
            (
                f"git diff {base_ref}..{branch} failed: "
                f"{(diff_proc.stderr or '').strip()[:200]}"
            ),
        )

    # Stat summary is cheap and helps the Planner / report skim the diff size.
    stat_text = None
    try:
        stat_proc = subprocess.run(
            ["git", "-C", str(repo), "diff", "--shortstat", f"{base_ref}..{branch}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if stat_proc.returncode == 0:
            stat_text = stat_proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return diff_proc.stdout, stat_text, None

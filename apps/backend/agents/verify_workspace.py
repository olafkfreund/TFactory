"""Packed-workspace round trip for the verify Job (RFC-0017 step 3, TFactory #1159).

The verify Job co-mounts the workspaces PVC today. That PVC is RWO ``local-path``,
so the Job must land on whichever node holds the worktree — the single reason this
fleet cannot verify across nodes, and the reason concurrency tops out at ~3-4
tasks. AIFactory's *build* path already runs packed in production
(``AIFACTORY_PACK_WORKSPACE``); this is the verify side of the same mechanism.

Three moves, all of them thin wrappers over the vendored
``tools.runners.artifact_store`` (there is exactly one packer in this fleet and
this is not it):

``pack_for_dispatch``   control plane, before dispatch — tar the spec tree (and
                        the base clone its linked worktree points at) to MinIO
                        and hand back the ``s3://`` URI.
``restore_workspace``   in-Job, before anything reads the spec — unpack
                        ``WORKSPACE_URI`` into ``WORKSPACE_ROOT``.
``push_back_workspace`` in-Job, after the pipeline — repack that same root so the
                        evidence outlives the pod.

**Why the push-back is the load-bearing half.** ``workdir`` on the packed path is
an ``emptyDir``: it dies with the Job. Screenshots, junit, coverage and
``status.json`` written there are gone unless something explicitly puts them back.
On the co-mounted path they survived incidentally, because the PVC outlived the
pod, so nothing in the current code ever had to be deliberate about it. That exact
omission already cost the AIFactory side one silent data-loss bug — a Job that
loses its evidence looks identical, at the Job level, to one that worked.

Hence the deliberate asymmetry in the error handling here: ``pack_for_dispatch``
is **fail-open** (no object store → return ``None`` → the caller keeps today's PVC
co-mount, nothing is lost), while ``restore_workspace`` and
``push_back_workspace`` **raise**. On the packed path a failed restore means the
Job has no spec to verify, and a failed push-back means the run's evidence is
already gone; both must fail the Job loudly rather than produce a green run with
nothing behind it.

Raising is only half of that, though (#1243): the pod's exit code is not what the
control plane reads. ``verify_pipeline.main`` catches both raises and turns them
into the DURABLE verdict — a terminal job-state row with ``has_verdict=False`` and
the reason — because ``reconcile_and_reap_once`` never probes a Job once a
terminal row exists, so a row that already said ``done`` could not be corrected.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Set on the Job by kube_sandbox.build_job_manifest / verify_dispatch. WORKSPACE_URI
# is the fleet-wide name from apis/concurrency-conventions.md §2; WORKSPACE_ROOT is
# where this service unpacks it (the Job's ``workdir``), because the spec/project
# arguments the pipeline receives are absolute paths under that mount.
ENV_WORKSPACE_URI = "WORKSPACE_URI"
ENV_WORKSPACE_ROOT = "WORKSPACE_ROOT"

_SERVICE = "tfactory"

# #1160 — control-plane restore of a pushed-back workspace.
PUSHED_BACK_MARKER = ".pushed_back.json"
RESTORED_SENTINEL = ".workspace_restored"
_RESTORE_ATTEMPTS = ".workspace_restore_attempts"
ENV_RESTORE_MAX_ATTEMPTS = "TFACTORY_WORKSPACE_RESTORE_MAX_ATTEMPTS"
_RESTORE_MAX_ATTEMPTS_DEFAULT = 20  # ≈5 min at the reconcile loop's 15 s tick
# Never copied back from an archive: the control plane's own bookkeeping, and
# the marker, which vouches for ONE archive — on the PVC the next dispatch
# would pack it and it would vouch for a Job that never pushed back.
_NEVER_RESTORED = frozenset(
    {"worker_ref.json", PUSHED_BACK_MARKER, RESTORED_SENTINEL, _RESTORE_ATTEMPTS}
)


def _open_store() -> tuple[Any, Any] | None:
    """``(ArtifactStore, StoreConfig)`` for the configured endpoint, or None.

    Lazy import: the store pulls boto3, and the co-mounted path must keep working
    on an image that has neither.
    """
    from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
        ArtifactStore,
        StoreConfig,
    )

    cfg = StoreConfig.from_env()
    if not cfg.endpoint:
        return None
    return ArtifactStore(cfg), cfg


def _workspace_ref(job_id: str, correlation_key: str | int | None, bucket: str) -> Any:
    """The one ``workspace``-role ref for this job.

    Dispatch and push-back deliberately use the SAME key: the Job repacks the root
    it unpacked, so the object is a drop-in replacement for the one it came from
    and a reader needs no second coordinate to find the results.
    """
    from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
        ArtifactRef,
    )

    return ArtifactRef(
        _SERVICE,
        job_id,
        "workspace",
        correlation_key=correlation_key,
        bucket=bucket,
    )


def _git_main_repo(project_dir: Path) -> Path | None:
    """The base clone that owns ``project_dir``, when it is a *linked* worktree.

    TFactory's layout puts the spec tree at ``workspaces/<uuid>/specs/<spec>/.worktree``
    while its clone is a SIBLING at ``workspaces/<project-name>`` (agents.utils
    ``_find_main_repo``). The clone is therefore NOT under the spec tree, so packing
    the spec tree alone ships a worktree whose ``.git`` pointer resolves to nothing
    and every git call in the Job fails. Returns None when ``project_dir`` is a
    plain checkout (its ``.git`` is a directory and travels with it).
    """
    dot_git = project_dir / ".git"
    try:
        if not dot_git.is_file():
            return None
        pointer = dot_git.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = Path(pointer.split(":", 1)[1].strip())
    for parent in gitdir.parents:
        if parent.name == ".git":
            return parent.parent
    return None


def _relative_roots(paths: list[Path | None], data_root: Path) -> list[str]:
    """Data-root-relative roots to pack, with nested ones folded into their parent.

    Everything is keyed off the data root so the unpacked tree in the Job has the
    same relative layout: the mount-relative ``--spec`` / ``--project`` paths the
    dispatcher computes stay correct without translation.
    """
    rels: list[str] = []
    for path in paths:
        if path is None:
            continue
        try:
            rel = Path(path).resolve().relative_to(data_root)
        except (ValueError, OSError):
            _log.warning(
                "[verify-workspace] %s is outside the data root %s; not packed",
                path,
                data_root,
            )
            continue
        rels.append(rel.as_posix())
    return sorted(
        {
            rel
            for rel in rels
            if not any(other != rel and rel.startswith(f"{other}/") for other in rels)
        }
    )


def _link_or_copy(src: str, dst: str, *, follow_symlinks: bool = True) -> None:
    """Hardlink a file into the staging tree, copying only when that is impossible.

    The base clone is a whole repo; copying it byte-for-byte just to feed the
    tarball would double the write cost of every dispatch. Hardlinks are free and
    the staging tree is read-only and short-lived.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)


def _stage(rels: list[str], data_root: Path, staging: Path) -> None:
    for rel in rels:
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            data_root / rel,
            dest,
            symlinks=True,
            copy_function=_link_or_copy,
            dirs_exist_ok=True,
        )


def pack_for_dispatch(
    *,
    spec_dir: Path,
    project_dir: Path,
    data_root: str,
    job_id: str,
    correlation_key: str | int | None = None,
) -> str | None:
    """Pack the spec workspace to MinIO; return its ``s3://`` URI, or None.

    Fail-open on purpose: None means "the packed path is not available", and the
    caller falls back to today's PVC co-mount. Nothing is lost by that — the
    co-mount is what production runs today.
    """
    try:
        opened = _open_store()
        if opened is None:
            _log.info(
                "[verify-workspace] S3_ENDPOINT unset; not packing job_id=%s "
                "(dispatch keeps the PVC co-mount)",
                job_id,
            )
            return None
        store, cfg = opened
        root = Path(data_root).resolve()
        rels = _relative_roots(
            [spec_dir, project_dir, _git_main_repo(project_dir)], root
        )
        if not rels:
            _log.warning(
                "[verify-workspace] nothing to pack for job_id=%s under %s",
                job_id,
                root,
            )
            return None

        from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
            pack_workspace,
        )

        with tempfile.TemporaryDirectory(prefix="tf-pack-") as tmp:
            staging = Path(tmp) / "workspace"
            _stage(rels, root, staging)
            uri = pack_workspace(
                store, _workspace_ref(job_id, correlation_key, cfg.bucket), staging
            )
    except Exception:  # noqa: BLE001 - a pack gap falls back, it never strands
        _log.warning(
            "[verify-workspace] packing failed for job_id=%s; "
            "dispatch keeps the PVC co-mount",
            job_id,
            exc_info=True,
        )
        return None
    _log.info("[verify-workspace] packed %s for job_id=%s to %s", rels, job_id, uri)
    return uri


def restore_workspace(
    *, uri: str | None = None, root: str | None = None
) -> Path | None:
    """In-Job: unpack ``WORKSPACE_URI`` into ``WORKSPACE_ROOT``. Returns the root.

    None (and no side effects) on the co-mounted path, where neither env is set —
    that is how the caller tells the two paths apart. Raises on the packed path:
    without the workspace there is no spec to verify, and a Job that carries on
    would report a verdict about an empty tree.
    """
    uri = (uri if uri is not None else os.environ.get(ENV_WORKSPACE_URI, "")).strip()
    root = (
        root if root is not None else os.environ.get(ENV_WORKSPACE_ROOT, "")
    ).strip()
    if not uri:
        return None
    if not root:
        raise RuntimeError(
            f"{ENV_WORKSPACE_URI} is set but {ENV_WORKSPACE_ROOT} is not; "
            "cannot tell where to unpack the workspace"
        )
    opened = _open_store()
    if opened is None:
        raise RuntimeError(
            f"{ENV_WORKSPACE_URI} is set but S3_ENDPOINT is not; "
            "the packed workspace cannot be fetched"
        )
    store, _cfg = opened

    from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
        unpack_workspace,
    )

    dest = Path(root)
    dest.mkdir(parents=True, exist_ok=True)
    unpack_workspace(store, uri, dest)
    _log.info("[verify-workspace] unpacked %s into %s", uri, dest)
    return dest


def push_back_workspace(
    *,
    root: Path,
    job_id: str,
    correlation_key: str | int | None = None,
) -> str:
    """In-Job: repack ``root`` to the job's workspace key. Returns the URI.

    THE step this whole change exists for. ``root`` is an ``emptyDir`` — every
    screenshot, junit file, coverage report and ``status.json`` the verify just
    produced is deleted with the pod unless this call lands. Raises on any failure
    so the Job fails: a green Job with no evidence is the failure mode that is
    invisible, and it is the one the acceptance for #1159 is written against.
    """
    opened = _open_store()
    if opened is None:
        raise RuntimeError(
            "S3_ENDPOINT is not set; the packed workspace cannot be pushed back "
            f"and the evidence under {root} would be lost with the pod"
        )
    store, cfg = opened

    from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
        pack_workspace,
    )

    uri = pack_workspace(
        store, _workspace_ref(job_id, correlation_key, cfg.bucket), root
    )
    _log.info(
        "[verify-workspace] pushed the workspace back for job_id=%s to %s",
        job_id,
        uri,
    )
    return uri


def mark_pushed_back(spec_dir: Path, job_id: str) -> None:
    """In-Job, just before ``push_back_workspace``: vouch for the archive (#1160).

    Dispatch and push-back share one object key, so a Job that died before
    pushing back leaves the PRE-dispatch archive under it. This marker, inside
    the archive and naming the job, is what tells the control plane the object
    is this Job's result. The upload is a single put, so a push-back cut short
    leaves the old object — without this job's marker.
    """
    from datetime import UTC, datetime  # noqa: PLC0415 - lazy by design

    (Path(spec_dir) / PUSHED_BACK_MARKER).write_text(
        json.dumps({"job_id": job_id, "pushed_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _marker_names(path: Path, job_id: str) -> bool:
    return _read_json(path).get("job_id") == job_id


def restore_outcome(spec_dir: Path, job_id: str) -> bool | None:
    """The recorded restore outcome for THIS job, or None if not yet decided.

    Scoped to the job because the spec dir outlives it: a rerun of the same spec
    is a new Job, and an earlier job's sentinel must not skip it.
    """
    data = _read_json(Path(spec_dir) / RESTORED_SENTINEL)
    if data.get("job_id") != job_id:
        return None
    return bool(data.get("restored"))


def _write_sentinel(spec_dir: Path, **fields: Any) -> None:
    from datetime import UTC, datetime  # noqa: PLC0415 - lazy by design

    fields["at"] = datetime.now(UTC).isoformat()
    (spec_dir / RESTORED_SENTINEL).write_text(json.dumps(fields), encoding="utf-8")


def _copy_spec_tree(src: Path, dst: Path, skip_dir: Path | None) -> int:
    """Copy ``src`` over ``dst``, per file via a temp sibling + rename, so a reader
    never sees a half-written ``status.json``. ``skip_dir`` (relative) is left
    alone entirely — the project worktree, which lives inside the spec dir."""
    copied = 0
    for root, dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        if skip_dir is not None:
            dirs[:] = [d for d in dirs if rel_root / d != skip_dir]
        for name in files:
            rel = rel_root / name
            if rel_root == Path() and name in _NEVER_RESTORED:
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tf-restore")
            shutil.copy2(Path(root) / name, tmp, follow_symlinks=False)
            tmp.replace(target)
            copied += 1
    return copied


def _restore_max_attempts() -> int:
    """``TFACTORY_WORKSPACE_RESTORE_MAX_ATTEMPTS``, never fatal: a malformed value
    falls back to the default instead of raising on every tick and leaving the
    spec never restored. At least 1."""
    raw = os.environ.get(ENV_RESTORE_MAX_ATTEMPTS)
    try:
        return max(1, int(raw)) if raw else _RESTORE_MAX_ATTEMPTS_DEFAULT
    except ValueError:
        _log.warning(
            "[verify-workspace] %s=%r is not an integer; using %d",
            ENV_RESTORE_MAX_ATTEMPTS,
            raw,
            _RESTORE_MAX_ATTEMPTS_DEFAULT,
        )
        return _RESTORE_MAX_ATTEMPTS_DEFAULT


def restore_spec_from_workspace(
    *,
    spec_dir: Path,
    project_dir: Path,
    job_id: str,
    uri: str,
    data_root: str,
) -> bool | None:
    """Control plane: bring a packed Job's spec tree back onto the PVC (#1160).

    Returns True when restored, False when there was nothing to restore (the
    Job never pushed back) or the retry budget is spent, and None when a
    transient failure should be retried on the next tick. Idempotent: once the
    sentinel exists, returns its outcome without touching anything.

    Only the spec dir comes back, minus the project worktree inside it: the
    archive also carries the worktree and the shared base clone, and one Job's
    copy of either would corrupt them for every other spec.
    """
    spec_dir = Path(spec_dir)
    done = restore_outcome(spec_dir, job_id)
    if done is not None:
        return done
    limit = _restore_max_attempts()
    try:
        root = Path(data_root).resolve()
        rel = spec_dir.resolve().relative_to(root)
        try:
            skip = Path(project_dir).resolve().relative_to(spec_dir.resolve())
        except ValueError:
            skip = None  # the worktree is elsewhere: nothing inside to protect
        opened = _open_store()
        if opened is None:
            raise RuntimeError("S3_ENDPOINT is not set; cannot fetch the workspace")
        store, _cfg = opened

        from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
            unpack_workspace,
        )

        with tempfile.TemporaryDirectory(prefix="tf-restore-") as tmp:
            unpack_workspace(store, uri, tmp)
            src = Path(tmp) / rel
            if not _marker_names(src / PUSHED_BACK_MARKER, job_id):
                _log.warning(
                    "[verify-workspace] job_id=%s never pushed its workspace back "
                    "(no marker for this job in %s); leaving %s as the reaper left it",
                    job_id,
                    uri,
                    spec_dir,
                )
                _write_sentinel(
                    spec_dir,
                    restored=False,
                    job_id=job_id,
                    reason="no push-back: the object is still the dispatch-time pack",
                )
                return False
            copied = _copy_spec_tree(src, spec_dir, skip)
    except Exception as exc:  # noqa: BLE001 - retried; never swallowed
        attempts_file = spec_dir / _RESTORE_ATTEMPTS
        prior = _read_json(attempts_file)
        attempts = (
            int(prior.get("attempts") or 0) + 1 if prior.get("job_id") == job_id else 1
        )
        attempts_file.write_text(
            json.dumps({"job_id": job_id, "attempts": attempts}), encoding="utf-8"
        )
        _log.error(
            "[verify-workspace] restoring job_id=%s into %s failed (attempt %d/%d)",
            job_id,
            spec_dir,
            attempts,
            limit,
            exc_info=True,
        )
        if attempts >= limit:
            _write_sentinel(
                spec_dir,
                restored=False,
                job_id=job_id,
                error=str(exc),
                attempts=attempts,
            )
            return False
        return None
    _write_sentinel(spec_dir, restored=True, job_id=job_id, files=copied)
    _log.info(
        "[verify-workspace] restored %d file(s) for job_id=%s into %s",
        copied,
        job_id,
        spec_dir,
    )
    return True

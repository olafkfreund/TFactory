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
"""

from __future__ import annotations

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

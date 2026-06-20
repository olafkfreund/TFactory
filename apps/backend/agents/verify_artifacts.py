"""Emit a verify's key artifacts to the object store (RFC-0016 #190).

When a verify reaches a terminal state the control plane records a durable
``job-state`` row (TFactory #465). This module uploads that verify's tangible
outputs — the Evaluator/Triager findings (``findings/verdicts.json``), any test
reports (jUnit XML), and the evidence tree (``findings/evidence/...``) — to the
shared MinIO object store via the vendored ``artifact_store`` client, and returns
the resulting ``artifacts[]`` records (URIs, never blobs) for stamping onto the
job-state row.

Per apis/concurrency-conventions.md §2 the key layout is
``tfactory/<correlation_key>/<job_id>/<role>[/<path>]`` in bucket
``factory-artifacts``; findings/reports use role ``test-report`` and evidence
files use role ``evidence``.

**Fail-open by design.** Object storage is an enhancement, not part of the
verdict: every public entry point wraps the upload in try/except and returns an
empty list (or whatever it uploaded so far) on any error — a missing client
(no boto3), unset ``S3_ENDPOINT``, or an unreachable MinIO MUST NEVER change or
block the verdict the verify already wrote to the workspace. The transport is
configured from the environment (``S3_ENDPOINT`` / ``S3_BUCKET`` /
``S3_ACCESS_KEY`` / ``S3_SECRET_KEY``) set on the pod by factory-gitops.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Cap how many evidence files we upload per verify so a pathological run can't
# stream thousands of objects on the terminal path (best-effort, bounded).
_MAX_EVIDENCE_FILES = 200


def _artifact_record(uri: str, role: str, path: Path) -> dict[str, Any]:
    """Build one apis/job-state.schema.json ``artifacts[]`` item from an upload."""
    ctype, _ = mimetypes.guess_type(path.name)
    try:
        size: int | None = path.stat().st_size
    except OSError:
        size = None
    return {"role": role, "uri": uri, "content_type": ctype, "bytes": size}


def emit_verify_artifacts(
    spec_dir: Path | str,
    *,
    job_id: str,
    correlation_key: str | int | None = None,
) -> list[dict[str, Any]]:
    """Upload a verify's findings + evidence to object storage; return ``artifacts[]``.

    Best-effort and fail-open: any failure (no boto3, no ``S3_ENDPOINT``,
    unreachable MinIO, missing files) is logged and yields whatever was uploaded
    so far — never raises, so the verdict is never blocked. ``correlation_key``
    is the RFC-0001 issue number when known; it threads the key so the object is
    joinable across services.
    """
    spec_dir = Path(spec_dir)
    artifacts: list[dict[str, Any]] = []
    try:
        # Lazy: importing the store (and boto3) only on the terminal path keeps
        # the verify hot loop dep-free and lets the no-S3 dev path no-op cleanly.
        from tools.runners.artifact_store import (  # noqa: PLC0415 - lazy by design
            ArtifactRef,
            ArtifactStore,
            StoreConfig,
        )

        cfg = StoreConfig.from_env()
        if not cfg.endpoint:
            _log.info(
                "[verify-artifacts] S3_ENDPOINT unset; skipping artifact upload "
                "for job_id=%s (verdict unaffected)",
                job_id,
            )
            return artifacts
        store = ArtifactStore(cfg)

        findings = spec_dir / "findings"

        # 1. The Evaluator/Triager verdicts blob — the canonical test report.
        verdicts = findings / "verdicts.json"
        if verdicts.is_file():
            ref = ArtifactRef(
                "tfactory",
                job_id,
                "test-report",
                correlation_key=correlation_key,
                path="verdicts.json",
                bucket=cfg.bucket,
            )
            uri = store.put_artifact(ref, verdicts, content_type="application/json")
            artifacts.append(_artifact_record(uri, "test-report", verdicts))

        # 2. Test reports: jUnit XML anywhere under the spec workspace.
        for report in sorted(spec_dir.rglob("*.xml")):
            if not report.is_file():
                continue
            rel = report.relative_to(spec_dir).as_posix()
            ref = ArtifactRef(
                "tfactory",
                job_id,
                "test-report",
                correlation_key=correlation_key,
                path=rel,
                bucket=cfg.bucket,
            )
            uri = store.put_artifact(ref, report, content_type="application/xml")
            artifacts.append(_artifact_record(uri, "test-report", report))

        # 3. Evidence tree (screenshots, recordings, http traces): bounded.
        evidence_root = findings / "evidence"
        if evidence_root.is_dir():
            evidence_files = sorted(p for p in evidence_root.rglob("*") if p.is_file())
            for count, ev in enumerate(evidence_files):
                if count >= _MAX_EVIDENCE_FILES:
                    _log.warning(
                        "[verify-artifacts] evidence cap (%d) hit for job_id=%s; "
                        "remaining evidence files not uploaded",
                        _MAX_EVIDENCE_FILES,
                        job_id,
                    )
                    break
                rel = ev.relative_to(evidence_root).as_posix()
                ctype, _ = mimetypes.guess_type(ev.name)
                ref = ArtifactRef(
                    "tfactory",
                    job_id,
                    "evidence",
                    correlation_key=correlation_key,
                    path=rel,
                    bucket=cfg.bucket,
                )
                uri = store.put_artifact(ref, ev, content_type=ctype)
                artifacts.append(_artifact_record(uri, "evidence", ev))

        if artifacts:
            _log.info(
                "[verify-artifacts] uploaded %d artifact(s) for job_id=%s to %s",
                len(artifacts),
                job_id,
                cfg.bucket,
            )
    except Exception:  # noqa: BLE001 — artifact emission must never block a verdict
        _log.warning(
            "[verify-artifacts] artifact upload failed for job_id=%s "
            "(continuing; verdict unaffected)",
            job_id,
            exc_info=True,
        )
    return artifacts

"""Send a correction hand-back to AIFactory (#185).

P4 of epic #182. Wraps the pure builder (``request.py``) with the side-effecting
half: it **always** writes the hand-back artifacts to ``findings/`` and, only
when explicitly opted in, POSTs the fix-request to AIFactory's receiver (P3 —
``POST /api/tasks/{task_id}/apply-correction``).

Two independent gates, mirroring the Triager's ``git_writer`` / ``pr_comment``:

  - ``dry_run`` (default ``True``)  — artifacts written, nothing sent.
  - ``confirm`` (default ``False``) — the second gate the interactive skill (P5)
    flips after showing the operator the preview.

A send only happens when ``dry_run is False and confirm``. Transport failures are
swallowed into a graceful ``SendResult`` — a hand-back must never break the
pipeline (best-effort, like the completion webhook).

``sender_fn`` is the injectable AIFactory client; tests pass a fake so the suite
never touches a real AIFactory. The default posts via stdlib ``urllib`` (no new
dependency).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .render import render_fix_request_md
from .request import CorrectionRequest

__all__ = ["CONTRACT_VERSION", "SendResult", "default_sender", "send_correction"]

Sender = Callable[[dict], dict]

# Version of the typed handback triage contract (#283), bumped like RFC-0002
# when the shape changes. Published schema:
# apps/backend/contracts/handback-triage-contract.v1.schema.json. AIFactory's
# #467 gate validates the POSTed ``triage`` block against the matching version.
CONTRACT_VERSION = "1.0"

# Path AIFactory POSTs back to when its QA Fixer finishes — closes the loop
# automatically (epic #182). Base URL is this TFactory web-server.
_CALLBACK_PATH = "/api/handback/aifactory-complete"


def _self_callback_url() -> str:
    """This TFactory's inbound completion-webhook URL for AIFactory to call back.

    Base from ``TFACTORY_SELF_API_URL`` (default the local web-server on :3103),
    mirroring the snapshotter's ``TFACTORY_AIFACTORY_API_URL`` default pattern.
    """
    base = (os.environ.get("TFACTORY_SELF_API_URL") or "http://localhost:3103").rstrip(
        "/"
    )
    return f"{base}{_CALLBACK_PATH}"


@dataclass
class SendResult:
    ok: bool
    dry_run: bool
    sent: bool
    task_id: str
    artifact_md: str | None = None
    artifact_json: str | None = None
    response: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _correlation_key_for(spec_dir: Path) -> str | None:
    """RFC-0001 correlation key for the hand-back (#249).

    Precedence mirrors the Triager's completion-event key: RFC-0002 contract
    ``correlation_key`` → ``issue_number`` from source.json → None (AIFactory
    falls back to the echoed ``tfactory_task_id``). Best-effort; never raises.
    """
    try:
        from agents.task_contract import read_task_contract

        contract = read_task_contract(spec_dir) or {}
        key = contract.get("correlation_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        source = json.loads((spec_dir / "context" / "source.json").read_text())
        issue = source.get("issue_number") or source.get("correlation_id")
        return str(int(issue)) if issue is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def default_sender(payload: dict) -> dict:
    """POST the fix-request to AIFactory's apply-correction route (P3).

    ``payload`` = ``{api_url, task_id, fix_request_md, source, confirm}``.
    Raises on transport error or non-2xx — ``send_correction`` catches it.
    """
    import urllib.error
    import urllib.request

    api_url = (payload.get("api_url") or "").rstrip("/")
    task_id = payload["task_id"]
    if not api_url:
        raise ValueError("no AIFactory api_url in source.json aifactory envelope")

    url = f"{api_url}/api/tasks/{task_id}/apply-correction"
    body = json.dumps(
        {
            "fix_request_md": payload["fix_request_md"],
            "source": payload.get("source"),
            "confirm": bool(payload.get("confirm")),
            # Echoed back by AIFactory to the callback URL to close the loop.
            "tfactory_task_id": payload.get("tfactory_task_id"),
            "tfactory_callback_url": payload.get("tfactory_callback_url"),
            # Typed handback triage contract (#283) — the structured report
            # AIFactory's QA-fixer gate (#467) schema-validates before acting,
            # plus the pinned assertion-manifest hash so each round provably
            # tests against the same bar. Both additive; AIFactory accepts the
            # legacy markdown-only POST when they're absent.
            "correlation_key": payload.get("correlation_key"),
            "triage": payload.get("triage"),
            "manifest_hash": payload.get("manifest_hash"),
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        raw = resp.read().decode() or "{}"
    return json.loads(raw)


def send_correction(
    request: CorrectionRequest,
    spec_dir: Path | str,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    sender_fn: Sender | None = None,
    now: str | None = None,
) -> SendResult:
    """Write the hand-back artifacts and optionally POST them to AIFactory.

    Artifacts (``findings/handback_request.{md,json}``) are **always** written
    when there is something to hand back. A POST happens only when
    ``dry_run is False and confirm`` (and the request is non-empty).
    """
    task_id = request.aifactory_task_id
    spec = Path(spec_dir)

    if request.nothing_to_hand_back:
        # Nothing to do — no artifacts, no send. Caller usually guards on this.
        return SendResult(ok=True, dry_run=dry_run, sent=False, task_id=task_id)

    findings = spec / "findings"
    findings.mkdir(parents=True, exist_ok=True)

    md = render_fix_request_md(request)
    md_path = findings / "handback_request.md"
    md_path.write_text(md)

    # RFC-0001 shared key so AIFactory reconciles the hand-back with the same
    # correlation as the completion event (#249).
    correlation_key = _correlation_key_for(spec)

    # Assertion pinning (#283): pin the suite that just failed as the bar for
    # this spec (idempotent — later cycles reuse the same manifest), and ride
    # its hash on the contract so each round provably tests the same assertions.
    # Best-effort — a missing/odd suite must never block the hand-back.
    manifest_hash: str | None = None
    try:
        from agents.handback.assertion_manifest import pin_manifest

        manifest_hash = pin_manifest(spec, spec / "tests").get("manifest_hash")
    except Exception:  # noqa: BLE001
        manifest_hash = None

    # The typed triage contract (#283) AIFactory's #467 gate validates: the
    # versioned, structured failure report + the pinned manifest hash +
    # correlation key. ``contract_version`` lets the consumer reject a shape it
    # doesn't understand (versioned like RFC-0002).
    triage = {
        "contract_version": CONTRACT_VERSION,
        **request.to_dict(),
        "manifest_hash": manifest_hash,
        "correlation_key": correlation_key,
    }

    json_doc = {
        **request.to_dict(),
        "generated_at": now or _now_iso(),
        "dry_run": dry_run,
        "fix_request_md_path": "findings/handback_request.md",
        "correlation_key": correlation_key,
        "manifest_hash": manifest_hash,
    }
    json_path = findings / "handback_request.json"
    json_path.write_text(json.dumps(json_doc, indent=2))

    result = SendResult(
        ok=True,
        dry_run=dry_run,
        sent=False,
        task_id=task_id,
        artifact_md=str(md_path),
        artifact_json=str(json_path),
    )

    if not (not dry_run and confirm):
        return result  # prepared only — the dry-run / unconfirmed path

    payload = {
        "api_url": (request.aifactory or {}).get("api_url"),
        "task_id": task_id,
        "fix_request_md": md,
        "source": request.source_kind,
        "confirm": True,
        # Self-reference so AIFactory can call us back when the fix is done
        # (epic #182 auto-loop). task_id IS the TFactory workspace key.
        "tfactory_task_id": task_id,
        "tfactory_callback_url": _self_callback_url(),
        # RFC-0001 shared correlation key (#249) — reconcile on this end-to-end.
        "correlation_key": correlation_key,
        # Typed handback triage contract + assertion-manifest hash (#283).
        "triage": triage,
        "manifest_hash": manifest_hash,
    }
    try:
        result.response = (sender_fn or default_sender)(payload)
        result.sent = True
    except Exception as exc:  # best-effort — never raise into the pipeline
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
    return result

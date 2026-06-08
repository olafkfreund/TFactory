"""Backstage TechInsights emitter for per-component test quality (#240, epic #232).

Backstage already knows TFactory *the service* (catalog-info.yaml) but nothing
publishes test-quality data about the systems TFactory *tests*. This module
emits a per-component fact on terminal status — accept-rate, the C1/C2
confidence rollup, and flaky count — so a Backstage Scorecard / TechInsights
check can grade the system-under-test.

Design (mirrors the completion-event + handback senders):
  - Opt-in + best-effort: a no-op unless ``TFACTORY_BACKSTAGE_TECHINSIGHTS_URL``
    is set, and every failure is swallowed — emitting must never break a run.
  - Pure-ish: the network call is behind a ``poster`` seam so tests never touch
    a real Backstage. The default posts via stdlib ``urllib`` (no new dep).
  - The entity ref is derived from the snapshotted repo (or overridden via
    ``TFACTORY_BACKSTAGE_COMPONENT``) — this is the SUT component, not TFactory.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_FACT_NAME = "tfactory.test_quality"
_DEFAULT_TIMEOUT = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _techinsights_url() -> str | None:
    url = (os.environ.get("TFACTORY_BACKSTAGE_TECHINSIGHTS_URL") or "").strip()
    return url.rstrip("/") or None


def _token() -> str | None:
    tok = (os.environ.get("TFACTORY_BACKSTAGE_TOKEN") or "").strip()
    return tok or None


def _component_ref(source: dict) -> str | None:
    """The SUT's Backstage entity ref (``component:default/<name>``).

    ``TFACTORY_BACKSTAGE_COMPONENT`` overrides — accepts a full ref
    (``component:default/foo``, used verbatim) or a bare name (wrapped). Else
    derive the name from the snapshotted repo slug (``owner/repo`` → ``repo``).
    """
    override = (os.environ.get("TFACTORY_BACKSTAGE_COMPONENT") or "").strip()
    if override:
        return override if ":" in override else f"component:default/{override.lower()}"
    slug = source.get("repo_slug") or source.get("repo") or ""
    name = str(slug).strip().rstrip("/").split("/")[-1].lower()
    if not name:
        return None
    return f"component:default/{name}"


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _flaky_count(verdicts: list) -> int:
    n = 0
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        flaky = (v.get("signals_summary") or {}).get("flaky")
        if (
            isinstance(flaky, dict)
            and str(flaky.get("classification")).lower() == "flaky"
        ):
            n += 1
    return n


def build_facts(spec_dir: Path, status: dict) -> dict:
    """Assemble the per-component test-quality facts from status + verdicts.json.

    Counts come from ``status.json`` (authoritative for the run); the confidence
    rollup + flaky count come from ``findings/verdicts.json`` (#238/#239).
    """
    doc = _load_json(spec_dir / "findings" / "verdicts.json")
    verdicts = doc.get("verdicts") if isinstance(doc.get("verdicts"), list) else []
    conf = (
        doc.get("confidence_summary")
        if isinstance(doc.get("confidence_summary"), dict)
        else {}
    )

    verdicts_count = int(status.get("verdicts_count") or len(verdicts) or 0)
    committed = int(status.get("committed_count") or 0)
    flagged = int(status.get("flagged_count") or 0)
    rejected = int(status.get("rejected_count") or 0)
    accept_rate = round(committed / verdicts_count, 4) if verdicts_count else 0.0

    return {
        "accept_rate": accept_rate,
        "accepted_mean_confidence": conf.get("accepted_mean", 0.0),
        "mean_confidence": conf.get("mean", 0.0),
        "commit_readiness": conf.get("commit_readiness", "low"),
        "verdicts_count": verdicts_count,
        "committed_count": committed,
        "flagged_count": flagged,
        "rejected_count": rejected,
        "flaky_count": _flaky_count(verdicts),
    }


def default_poster(url: str, payload: dict, token: str | None) -> dict:
    """POST the fact to the Backstage TechInsights endpoint via stdlib urllib.

    Raises on transport error / non-2xx — ``maybe_emit_backstage`` catches it.
    """
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST", headers=headers
    )
    with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode() or "{}"
    return json.loads(raw) if raw.strip().startswith("{") else {"raw": raw}


def maybe_emit_backstage(
    spec_dir: Path,
    status: dict,
    *,
    poster: Callable[[str, dict, str | None], dict] | None = None,
    source: dict | None = None,
) -> dict:
    """Best-effort emit of the test-quality fact. Never raises.

    Returns a small result dict (``emitted`` + ``reason``/``entity``) for the
    caller's logs and for tests. No-op (``emitted=False``) when the URL env is
    unset or no SUT component can be resolved.
    """
    url = _techinsights_url()
    if not url:
        return {"emitted": False, "reason": "disabled"}
    if source is None:
        from agents.triager import _load_source_meta

        source = _load_source_meta(spec_dir)
    entity_ref = _component_ref(source)
    if not entity_ref:
        return {"emitted": False, "reason": "no_component"}

    payload = {
        "entityRef": entity_ref,
        "factName": _FACT_NAME,
        "timestamp": _now_iso(),
        "facts": build_facts(spec_dir, status),
    }
    try:
        response = (poster or default_poster)(url, payload, _token())
        return {"emitted": True, "entity": entity_ref, "response": response}
    except Exception as exc:  # noqa: BLE001 — emitting must never break the run
        return {"emitted": False, "reason": f"error: {exc}", "entity": entity_ref}

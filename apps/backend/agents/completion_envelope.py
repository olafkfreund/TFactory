"""Additive CloudEvents / idempotency / trace fields for the completion envelope (#282).

Part of epic #284 (reliable completion-event delivery). This upgrades the
RFC-0001 completion envelope TFactory emits with three **additive,
backward-compatible** field groups so CFactory can dedup and trace consistently
with the mirror upgrade on AIFactory:

1. ``id`` — a per-event UUIDv4 for consumer-side exactly-once dedup. Generated
   once when the envelope is built; the #281 outbox stores the built envelope
   and re-sends it verbatim, so the id is **stable across retries** (the relay's
   ``Idempotency-Key`` is this same id).
2. CloudEvents-core aligned fields (``specversion``, ``source``, ``type``,
   ``time``) alongside the existing ``service`` / ``status`` / ``updated_at`` /
   ``correlation_key``.
3. W3C ``traceparent`` (+ optional ``tracestate``) for OpenTelemetry correlation
   across the PARR stage — inherited from the ``TRACEPARENT`` / ``TRACESTATE``
   env when the agent runs inside a trace, else freshly generated.

Nothing is removed — RFC-conformant consumers ignore the extra fields (RFC §7).
Removal of any legacy field is a separate, final step tracked after all
consumers accept the new envelope.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Any, TypedDict

# CloudEvents spec version we align to.
CLOUDEVENTS_SPECVERSION = "1.0"

# Reverse-DNS event type for TFactory's terminal completion. Mirrors
# AIFactory's #466 (``io.factory.aifactory.completion``) under the shared
# ``io.factory`` namespace so CFactory routes both producers uniformly.
COMPLETION_EVENT_TYPE = "io.factory.tfactory.completion"

# W3C trace-context ``traceparent``: version "-" trace-id(32hex) "-" span-id(16hex)
# "-" flags(2hex). https://www.w3.org/TR/trace-context/#traceparent-header
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


class CompletionCorrelation(TypedDict, total=False):
    """RFC-0001 §4 optional chain block (upstream/downstream links)."""

    issue_number: int | None
    spec_id: str | None
    branch: str | None
    pr_number: int | None


class CompletionEvidence(TypedDict):
    """RFC-0001a evidence block — what the verify actually produced."""

    proof_kind: str
    verdicts: int
    accepted: int
    flagged: int
    rejected: int


class CompletionEnvelope(TypedDict, total=False):
    """The v1 normalized completion-event envelope TFactory emits (#198).

    Built by :func:`agents.triager._build_completion_envelope`. The RFC-0001
    core fields (``correlation_key``, ``service``, ``task_id``, ``status``,
    ``phase``, ``updated_at``) are always populated; every other key is additive
    and may be absent, so the whole shape is ``total=False`` (the builder is the
    single source of truth for which keys are present). This is a *typing-only*
    description — it does not change the serialized JSON.
    """

    # RFC-0001 core (Factory#4) — always present in practice.
    correlation_key: str
    service: str
    task_id: str
    status: str | None
    phase: str
    updated_at: str
    # RFC-0001 §4 optional chain block.
    correlation: CompletionCorrelation
    # Additive normalized header + #85/#198 flat fields.
    schema_version: str
    event: str
    correlation_id: int | None
    project_id: str | None
    spec_id: str | None
    outcome: str
    repo: str | None
    branch: str | None
    pr_number: int | None
    result: dict[str, Any]
    usage: dict[str, Any]
    emitted_at: str
    # #282 CloudEvents-core + idempotency id + W3C trace context.
    id: str
    specversion: str
    source: str
    type: str
    time: str
    traceparent: str
    tracestate: str
    # RFC-0001a evidence + optional no-evidence reason.
    evidence: CompletionEvidence
    halt_reason: str
    # RFC-0007 / RFC-0006 best-effort annotations.
    access: dict[str, Any]
    verification: dict[str, Any]


def new_event_id() -> str:
    """Return a fresh UUIDv4 string — unique per completion event."""
    return str(uuid.uuid4())


def event_source(project_id: str | None = None) -> str:
    """CloudEvents ``source``: a URI-reference identifying the producer.

    ``TFACTORY_EVENT_SOURCE`` env override → ``/tfactory``. Deployment-level
    (not per-project) so CFactory routes by a stable producer identity; the
    per-event/per-project detail lives in ``correlation_key`` / ``project_id``.
    ``project_id`` is accepted for call-site symmetry but not interpolated.
    Always non-empty (CloudEvents requires a non-empty source).
    """
    override = (os.environ.get("TFACTORY_EVENT_SOURCE") or "").strip()
    return override or "/tfactory"


def _generate_traceparent() -> str:
    """Build a valid, fresh W3C ``traceparent`` (sampled).

    trace-id = 32 hex (uuid4), span-id = 16 hex (half of another uuid4),
    flags = ``01`` (sampled). Never all-zero (forbidden by the spec).
    """
    trace_id = uuid.uuid4().hex  # 32 hex
    span_id = uuid.uuid4().hex[:16]  # 16 hex
    return f"00-{trace_id}-{span_id}-01"


def traceparent() -> str:
    """Return the inherited ``TRACEPARENT`` if valid, else a fresh one.

    Mirrors AIFactory's ``core.tracing_bootstrap`` inheritance so a trace begun
    upstream (the spec hand-off) threads through TFactory's completion event.
    """
    inherited = (os.environ.get("TRACEPARENT") or "").strip()
    if inherited and _TRACEPARENT_RE.match(inherited):
        return inherited
    return _generate_traceparent()


def tracestate() -> str | None:
    """Return the inherited ``TRACESTATE`` vendor string, or ``None``."""
    value = (os.environ.get("TRACESTATE") or "").strip()
    return value or None


def cloudevents_fields(*, project_id: str | None, time_iso: str) -> dict:
    """Return the additive ``id`` + CloudEvents-core + trace fields.

    ``time_iso`` should be the envelope's emission timestamp (RFC3339) so
    CloudEvents ``time`` matches ``emitted_at``.
    """
    fields = {
        "id": new_event_id(),
        "specversion": CLOUDEVENTS_SPECVERSION,
        "source": event_source(project_id),
        "type": COMPLETION_EVENT_TYPE,
        "time": time_iso,
        "traceparent": traceparent(),
    }
    state = tracestate()
    if state:
        fields["tracestate"] = state
    return fields


def validate_cloudevents_core(envelope: dict) -> list[str]:
    """Return a list of conformance errors (empty = valid).

    Checks the CloudEvents-core required attributes (``id``, ``source``,
    ``type``, ``specversion``) are present + non-empty, and that ``traceparent``
    (when present) is a well-formed W3C trace-context value. Used by CI to
    guard the additive upgrade against regressions (#282 acceptance).
    """
    errors: list[str] = []
    for attr in ("id", "source", "type", "specversion"):
        value = envelope.get(attr)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty CloudEvents-core attribute: {attr!r}")
    if envelope.get("specversion") not in (None, CLOUDEVENTS_SPECVERSION):
        errors.append(
            f"unexpected specversion {envelope.get('specversion')!r} "
            f"(want {CLOUDEVENTS_SPECVERSION!r})"
        )
    tp = envelope.get("traceparent")
    if tp is not None and not _TRACEPARENT_RE.match(str(tp)):
        errors.append(f"malformed traceparent: {tp!r}")
    if tp is not None and isinstance(tp, str):
        # All-zero trace-id / span-id is explicitly invalid per the W3C spec.
        parts = tp.split("-")
        if len(parts) == 4 and (parts[1] == "0" * 32 or parts[2] == "0" * 16):
            errors.append("traceparent has all-zero trace-id or span-id")
    return errors


__all__ = [
    "CLOUDEVENTS_SPECVERSION",
    "COMPLETION_EVENT_TYPE",
    "CompletionCorrelation",
    "CompletionEnvelope",
    "CompletionEvidence",
    "cloudevents_fields",
    "event_source",
    "new_event_id",
    "traceparent",
    "tracestate",
    "validate_cloudevents_core",
]

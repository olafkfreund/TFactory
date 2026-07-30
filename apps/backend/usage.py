"""Token-usage accumulator for a test run (#224, RFC-0001 v1.1).

CFactory's *Tokens & cost* page reads an additive ``usage`` block off TFactory's
completion event (see :func:`agents.triager._build_completion_envelope`). This
module is the small, zeros-safe accumulator plus a tolerant normalizer that
pulls usage out of whatever shape a provider hands back (a Claude Agent SDK
``ResultMessage`` with a ``.usage`` dict and ``.total_cost_usd``, a plain dict,
an Anthropic-style ``Message`` with a ``.usage`` object, etc.).

Unlike PFactory's in-memory accumulator, a TFactory task spans many agent
sessions and can loop through handback retries, so the running total is
persisted on disk in the spec's ``status.json`` under a ``usage`` key. Each
session folds its final (cumulative) ``ResultMessage`` usage in via
:func:`record_in_status`; the completion event reads the sum back via
:func:`usage_block_from_status`. Additive and optional — zeros when no LLM ran.

Resolved-model evidence (#869)
------------------------------
The ``usage`` block also carries a ``workers`` list — the same shape AIFactory's
``token_usage.json`` uses (``worker_id`` / ``phase`` / ``provider`` / ``model``
/ tokens / cost), so CFactory renders both services' attribution identically.
TFactory's "worker" is an execution *phase* (``planning`` / ``coding`` / ``qa``);
verification runs on the ``coding`` key — there is no ``testing`` phase (see
``docs/model-attribution.md``).

Each record separates two DIFFERENT facts:

* ``requested_model`` — what the seam asked for (config / contract).
* ``model`` — what actually *served the tokens*, observed from the provider's
  own response. A session that fell back reports the model that ran, not the
  one that was asked for. Empty (never guessed) when the provider does not say.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

# Per-million-token USD pricing for known models, used to derive ``cost_usd``
# only when a usage source did not already supply a cost. Kept deliberately
# small; an unknown model simply yields cost 0.0 (never a guessed number).
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model id (prefix-matched): (input $/Mtok, output $/Mtok)
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-opus": (15.0, 75.0),
}


def _price_for(model: str) -> tuple[float, float] | None:
    """Return ``(input, output)`` $/Mtok for ``model`` by longest-prefix match."""
    if not model:
        return None
    best: tuple[int, tuple[float, float]] | None = None
    for prefix, price in _PRICE_PER_MTOK.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def estimate_cost_usd(input_tokens: int, output_tokens: int, model: str) -> float:
    """Best-effort cost from a known price table; 0.0 when the model is unknown."""
    price = _price_for(model)
    if price is None:
        return 0.0
    in_rate, out_rate = price
    return round((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000, 6)


class RunUsage(BaseModel):
    """Accumulated token usage + cost for one test run.

    Named ``RunUsage`` (not ``TestUsage``) so pytest doesn't try to collect it
    as a test case.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: RunUsage | None) -> None:
        """Fold another usage record in (no-op for ``None``)."""
        if other is None:
            return
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd = round(self.cost_usd + other.cost_usd, 6)
        # Keep the first non-empty model as the dominant id for the run.
        if other.model and not self.model:
            self.model = other.model

    def as_event_block(self) -> dict:
        """The additive ``usage`` block for the completion envelope (RFC-0001)."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "model": self.model,
        }


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def model_from_model_usage(model_usage: object) -> str:
    """The model id that actually SERVED the most tokens in a session.

    The Claude Agent SDK's ``ResultMessage`` has no ``.model`` attribute at all
    (see ``claude_agent_sdk.types``) — which is why ``usage.model`` came back
    empty on real Claude runs while the unit tests, using a fake that *did*
    have one, stayed green (#869). What it does carry is ``model_usage``: a
    ``{model_id: {inputTokens, outputTokens, ...}}`` breakdown of what really
    ran. A session that fell back mid-flight lists BOTH ids, so picking the id
    with the most tokens names the model that did the work rather than the one
    that was requested.

    Returns ``""`` when there is nothing to read — never a guess.
    """
    if not isinstance(model_usage, dict) or not model_usage:
        return ""

    def _served(record: object) -> int:
        if not isinstance(record, dict):
            return 0
        return _as_int(record.get("inputTokens")) + _as_int(record.get("outputTokens"))

    # Tie-break on the id so the choice is deterministic across runs.
    return str(max(model_usage, key=lambda k: (_served(model_usage[k]), str(k))))


def usage_from_obj(obj: object) -> RunUsage | None:
    """Tolerantly normalize a provider response into a :class:`RunUsage`.

    Handles the common shapes:

    * a Claude Agent SDK ``ResultMessage``: a ``.usage`` dict with
      ``input_tokens`` / ``output_tokens`` plus a sibling ``.total_cost_usd``
      and ``.model``;
    * a plain ``dict`` with ``input_tokens`` / ``output_tokens`` (optionally
      nested under a ``"usage"`` key), plus optional ``cost_usd`` / ``model``;
    * an Anthropic-style ``Message`` exposing a nested ``.usage`` object.

    Returns ``None`` when no token counts can be found, so callers can fold the
    result unconditionally via :meth:`RunUsage.add`. ``cost_usd`` is taken from
    the source when present, else derived from the price table.
    """
    if obj is None:
        return None

    # dict shape — possibly nested under "usage".
    if isinstance(obj, dict):
        data = obj.get("usage") if isinstance(obj.get("usage"), dict) else obj
        if not isinstance(data, dict):
            return None
        in_tok = _as_int(data.get("input_tokens") or data.get("prompt_tokens"))
        out_tok = _as_int(data.get("output_tokens") or data.get("completion_tokens"))
        if in_tok == 0 and out_tok == 0:
            return None
        model = str(obj.get("model") or data.get("model") or "") or (
            model_from_model_usage(obj.get("model_usage") or obj.get("modelUsage"))
        )
        cost = obj.get("total_cost_usd", data.get("cost_usd", obj.get("cost_usd")))
        cost_usd = (
            float(cost)
            if cost is not None
            else estimate_cost_usd(in_tok, out_tok, model)
        )
        return RunUsage(
            input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd, model=model
        )

    # object exposing a ``.usage`` (SDK ResultMessage dict / Anthropic Message).
    usage = getattr(obj, "usage", None)
    # ResultMessage has no ``.model``; its per-model breakdown is the observed
    # evidence of what ran (#869).
    model = str(getattr(obj, "model", "") or "") or model_from_model_usage(
        getattr(obj, "model_usage", None)
    )
    if isinstance(usage, dict):
        in_tok = _as_int(usage.get("input_tokens"))
        out_tok = _as_int(usage.get("output_tokens"))
    else:
        src = usage if usage is not None else obj
        in_tok = _as_int(getattr(src, "input_tokens", 0))
        out_tok = _as_int(getattr(src, "output_tokens", 0))
        if not model:
            model = str(getattr(src, "model", "") or "")
    if in_tok == 0 and out_tok == 0:
        return None
    cost = getattr(obj, "total_cost_usd", None)
    cost_usd = (
        float(cost) if cost is not None else estimate_cost_usd(in_tok, out_tok, model)
    )
    return RunUsage(
        input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost_usd, model=model
    )


# ─── status.json persistence (accumulates across sessions + handback retries) ──


def _status_path(spec_dir: Path | str) -> Path:
    return Path(spec_dir) / "status.json"


def _read_status(spec_dir: Path | str) -> dict:
    path = _status_path(spec_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _usage_from_status(status: dict) -> RunUsage:
    acc = status.get("usage") if isinstance(status.get("usage"), dict) else {}
    return RunUsage(
        input_tokens=_as_int(acc.get("input_tokens")),
        output_tokens=_as_int(acc.get("output_tokens")),
        cost_usd=float(acc.get("cost_usd") or 0.0),
        model=str(acc.get("model") or ""),
    )


def _provider_for(model: str) -> str:
    """Provider name for a model id, or ``""`` when it cannot be inferred.

    Deliberately reuses ``phase_config.infer_provider_from_model`` (the same
    pure function the execution seams use to pick a provider) rather than
    re-deriving the mapping here. Lazily imported and best-effort: bookkeeping
    must never break a run.
    """
    if not model:
        return ""
    try:
        from phase_config import infer_provider_from_model  # noqa: PLC0415

        return str(infer_provider_from_model(model) or "")
    except Exception:  # noqa: BLE001 - attribution is best-effort
        return ""


def _workers_from_status(status: dict) -> dict[str, dict]:
    """Re-index the persisted ``usage.workers`` list by ``worker_id``."""
    acc = status.get("usage") if isinstance(status.get("usage"), dict) else {}
    records = acc.get("workers") if isinstance(acc.get("workers"), list) else []
    return {
        str(r["worker_id"]): dict(r)
        for r in records
        if isinstance(r, dict) and r.get("worker_id")
    }


def _fold_worker(
    workers: dict[str, dict],
    *,
    phase: str,
    usage: RunUsage,
    requested_model: str,
    resolved_model: str,
) -> None:
    """Fold one session into its phase's record (in place, additive).

    Mirrors AIFactory's ``token_usage.json`` ``workers`` map so both services
    hand CFactory the same attribution shape. ``model`` is the OBSERVED id and
    is only ever overwritten by another observed id — a later session that
    could not observe one must not blank out evidence an earlier one captured.
    """
    record = workers.get(phase)
    if record is None:
        record = {
            "worker_id": phase,
            "phase": phase,
            "provider": "",
            "requested_model": "",
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        workers[phase] = record
    if requested_model:
        record["requested_model"] = requested_model
    if resolved_model:
        record["model"] = resolved_model
    # Provider comes from the REQUESTED id, because that is the string the seam
    # routes on: an `ollama:` / `studio:` prefix picks the adapter, and the id
    # the server echoes back has usually lost the prefix ("qwen3:14b").
    provider = _provider_for(str(record["requested_model"] or record["model"]))
    if provider:
        record["provider"] = provider
    record["input_tokens"] = _as_int(record["input_tokens"]) + usage.input_tokens
    record["output_tokens"] = _as_int(record["output_tokens"]) + usage.output_tokens
    record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
    record["cost_usd"] = round(float(record["cost_usd"] or 0.0) + usage.cost_usd, 6)


def record_in_status(
    spec_dir: Path | str,
    usage: RunUsage | None,
    *,
    phase: str | None = None,
    requested_model: str | None = None,
    resolved_model: str | None = None,
) -> None:
    """Fold one session's usage into the spec's persisted running total.

    ``phase`` (e.g. ``"coding"`` — the key verification runs under) opts the
    session into the per-phase ``usage.workers`` breakdown (#869), recording
    ``requested_model`` alongside the ``resolved_model`` that actually served
    the tokens. Omit it and the block is byte-identical to before.

    Best-effort: a missing/corrupt ``status.json`` starts the total from zero,
    and any write error is swallowed so token bookkeeping can never break a run.
    """
    usage = usage or RunUsage()
    # The provider's own answer; else the id the usage payload named. NEVER the
    # requested model — a request is not evidence that anything ran.
    observed = resolved_model or usage.model or ""
    has_tokens = bool(usage.input_tokens or usage.output_tokens)
    # A provider that reports no token counts at all (Ollama, the
    # OpenAI-compatible endpoints) still tells us WHICH model served the turn,
    # and that is the whole point of the record: without this, exactly the runs
    # the scorecard has least evidence for are the ones that write nothing.
    has_model_evidence = bool(phase) and bool(observed or requested_model)
    if not has_tokens and not has_model_evidence:
        return
    status = _read_status(spec_dir)
    running = _usage_from_status(status)
    running.add(usage)
    if observed and not running.model:
        running.model = observed
    block = running.as_event_block()
    workers = _workers_from_status(status)
    if phase:
        _fold_worker(
            workers,
            phase=phase,
            usage=usage,
            requested_model=requested_model or "",
            resolved_model=observed,
        )
    if workers:
        block["workers"] = [workers[k] for k in sorted(workers)]
    status["usage"] = block
    try:
        _status_path(spec_dir).write_text(json.dumps(status, indent=2))
    except OSError:
        pass


def usage_block_from_status(spec_dir: Path | str) -> dict:
    """The RFC-0001 ``usage`` block for the completion event (zeros when none).

    Carries the per-phase ``workers`` breakdown through to the event when one
    was recorded; the key is omitted entirely otherwise, so a run that observed
    no model is visibly blank rather than plausibly defaulted (#869).
    """
    status = _read_status(spec_dir)
    block = _usage_from_status(status).as_event_block()
    workers = _workers_from_status(status)
    if workers:
        block["workers"] = [workers[k] for k in sorted(workers)]
    return block

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
        model = str(obj.get("model") or data.get("model") or "")
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
    model = str(getattr(obj, "model", "") or "")
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


def record_in_status(spec_dir: Path | str, usage: RunUsage | None) -> None:
    """Fold one session's usage into the spec's persisted running total.

    Best-effort: a missing/corrupt ``status.json`` starts the total from zero,
    and any write error is swallowed so token bookkeeping can never break a run.
    """
    if usage is None or (usage.input_tokens == 0 and usage.output_tokens == 0):
        return
    status = _read_status(spec_dir)
    running = _usage_from_status(status)
    running.add(usage)
    status["usage"] = running.as_event_block()
    try:
        _status_path(spec_dir).write_text(json.dumps(status, indent=2))
    except OSError:
        pass


def usage_block_from_status(spec_dir: Path | str) -> dict:
    """The RFC-0001 ``usage`` block for the completion event (zeros when none)."""
    return _usage_from_status(_read_status(spec_dir)).as_event_block()

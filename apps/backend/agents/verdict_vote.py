"""Best-of-N majority vote over independent LLM judge calls (#649).

Single-pass LLM-as-judge verdicts have near coin-flip run-to-run variance,
so no single-pass LLM verdict may gate accept/deny alone. This module is the
one reusable primitive: call an async ``judge`` N times, extract a categorical
verdict from each result, and return the majority with the full vote record.

Fail-closed semantics: a judge crash, an extractor crash, or a ``None``/empty
extraction all count as ``fail_vote`` (a deny vote). Ties among the top-counted
values break toward the earliest entry in ``severity`` (most conservative
first), so a 1-1-1 accept/flag/reject split resolves to reject.

Pure orchestration - no SDK, no filesystem, no status side-effects. The one
GATING consumer today is the Evaluator judge session
(``agents.evaluator._run_evaluator_session``); deterministic signals stay the
primary deciders and are untouched.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["VoteResult", "majority_vote"]


@dataclass(frozen=True)
class VoteResult:
    """Outcome of one majority vote.

    Attributes:
        majority: the winning verdict value.
        votes: every vote cast, in call order (crashes already mapped to the
            fail vote).
        dissent: indices into ``votes`` that disagree with the majority.
        split: e.g. ``"3-0"`` or ``"2-1"`` (majority count vs the rest).
    """

    majority: str
    votes: tuple[str, ...]
    dissent: tuple[int, ...]
    split: str

    @property
    def unanimous(self) -> bool:
        return not self.dissent


async def majority_vote(
    judge: Callable[[int], Awaitable[Any]],
    extract: Callable[[Any], str | None],
    *,
    n: int = 3,
    fail_vote: str = "reject",
    severity: Sequence[str] = ("reject", "flag", "accept"),
) -> VoteResult:
    """Run ``judge`` ``n`` times and majority-vote the extracted verdicts.

    Args:
        judge: async callable; called as ``judge(i)`` for i in 0..n-1. Each
            call must be an independent judgment (same prompt, fresh call).
        extract: maps one judge result to a categorical verdict string.
        n: number of independent calls (minimum 1).
        fail_vote: the deny vote recorded when a call or extraction fails
            (fail-closed).
        severity: tie-break order, most conservative first.

    Returns:
        VoteResult with the majority, all votes, dissent indices and split.
    """
    n = max(1, n)
    votes: list[str] = []
    for i in range(n):
        try:
            vote = extract(await judge(i))
        except Exception:  # noqa: BLE001 - judge crash is a deny vote, fail-closed
            vote = None
        votes.append(vote if isinstance(vote, str) and vote else fail_vote)

    counts = Counter(votes)
    top = max(counts.values())

    def _rank(value: str) -> int:
        return severity.index(value) if value in severity else len(severity)

    majority = min((v for v, c in counts.items() if c == top), key=_rank)
    dissent = tuple(i for i, v in enumerate(votes) if v != majority)
    return VoteResult(
        majority=majority,
        votes=tuple(votes),
        dissent=dissent,
        split=f"{counts[majority]}-{n - counts[majority]}",
    )

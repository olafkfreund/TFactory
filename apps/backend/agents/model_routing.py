"""Read the RFC-0014 routed ``test_gen`` model + runtime from a contract.

RFC-0014 (cost-aware, capability-aware model & runtime routing) lets PFactory's
cost-aware router pick a per-role model and a runtime *before* execution, writing
them into the contract's ``execution`` block::

    "execution": {
      "phase_models": { "planning": "opus", "coding": "sonnet",
                        "qa": "haiku", "test_gen": "ollama:qwen" },
      "runtime": "claude"
    }

TFactory's test planner/generator consumes the ``test_gen`` entry so the suite is
generated with the cheap-but-capable model the router chose, and honours
``execution.runtime`` as the default runtime when the model string is provider-
ambiguous (e.g. a bare shorthand).

This module is **pure** and **additive**: when the block (or the ``test_gen``
key / ``runtime`` key) is absent, the readers return ``None`` and the caller
falls back to today's behaviour (``get_phase_model(..., "coding")``). Tolerant of
missing/extra keys — a schema bump never breaks ingest.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "KNOWN_RUNTIMES",
    "routed_test_gen_model",
    "runtime_from_contract",
]

# RFC-0014 §6 runtime allowlist. ``claude`` is the default; the others are gated
# OFF unless an operator enables them. TFactory only *reads* the declared runtime
# to disambiguate a provider-ambiguous model string — gating/enablement lives in
# AIFactory's provider registry, not here.
KNOWN_RUNTIMES: frozenset[str] = frozenset(
    {
        "claude",
        "codex",
        "antigravity",
        "ollama",
        "ollama-cloud",
        "claude-subagents",
        "dynamic-workflow",
    }
)


def _execution_block(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the contract's ``execution`` dict, or ``None`` when absent."""
    if not isinstance(contract, dict):
        return None
    execution = contract.get("execution")
    return execution if isinstance(execution, dict) else None


def routed_test_gen_model(contract: dict[str, Any] | None) -> str | None:
    """Return ``execution.phase_models.test_gen`` (a model string) or ``None``.

    The router writes the cheapest-capable model for test generation under the
    ``test_gen`` role. A missing ``execution`` block, a missing/empty
    ``phase_models`` map, or a non-string/blank ``test_gen`` value all yield
    ``None`` — the signal for the caller to keep its prior model selection
    (back-compat).
    """
    execution = _execution_block(contract)
    if execution is None:
        return None
    phase_models = execution.get("phase_models")
    if not isinstance(phase_models, dict):
        return None
    model = phase_models.get("test_gen")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def runtime_from_contract(contract: dict[str, Any] | None) -> str | None:
    """Return ``execution.runtime`` (normalized) when it is a known runtime.

    Returns ``None`` when the block is absent or the value is not in
    :data:`KNOWN_RUNTIMES`, so callers fall back to inferring the provider from
    the model string alone (today's behaviour).
    """
    execution = _execution_block(contract)
    if execution is None:
        return None
    runtime = execution.get("runtime")
    if isinstance(runtime, str):
        normalized = runtime.strip().lower()
        if normalized in KNOWN_RUNTIMES:
            return normalized
    return None

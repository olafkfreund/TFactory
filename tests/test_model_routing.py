"""Tests for the RFC-0014 routed ``test_gen`` model + runtime reader (#461).

The cost-aware router (RFC-0014) writes a per-role model map and a runtime into
the contract's ``execution`` block. TFactory's test generator consumes the
``test_gen`` entry; these tests pin the pure reader's behaviour and its
additive/back-compat fallbacks (absent block => ``None`` => today's behaviour).
"""

from __future__ import annotations

import pytest
from agents.model_routing import (
    KNOWN_RUNTIMES,
    routed_test_gen_model,
    runtime_from_contract,
)


def test_reads_routed_test_gen_model() -> None:
    contract = {
        "execution": {
            "phase_models": {
                "planning": "opus",
                "coding": "sonnet",
                "test_gen": "ollama:qwen",
            }
        }
    }
    assert routed_test_gen_model(contract) == "ollama:qwen"


def test_strips_whitespace_on_model() -> None:
    contract = {"execution": {"phase_models": {"test_gen": "  haiku  "}}}
    assert routed_test_gen_model(contract) == "haiku"


@pytest.mark.parametrize(
    "contract",
    [
        None,
        {},
        {"execution": None},
        {"execution": {}},
        {"execution": {"phase_models": None}},
        {"execution": {"phase_models": {}}},
        {"execution": {"phase_models": {"coding": "sonnet"}}},  # no test_gen key
        {"execution": {"phase_models": {"test_gen": ""}}},  # blank
        {"execution": {"phase_models": {"test_gen": "   "}}},  # whitespace only
        {"execution": {"phase_models": {"test_gen": 7}}},  # non-string
        {"execution": "not-a-dict"},
    ],
)
def test_absent_or_malformed_model_is_none(contract: object) -> None:
    # Back-compat: anything the router did not cleanly write yields None so the
    # caller keeps its prior model selection.
    assert routed_test_gen_model(contract) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "runtime,expected",
    [
        ("claude", "claude"),
        ("ollama", "ollama"),
        ("ollama-cloud", "ollama-cloud"),
        ("codex", "codex"),
        ("antigravity", "antigravity"),
        ("claude-subagents", "claude-subagents"),
        ("dynamic-workflow", "dynamic-workflow"),
        ("  CLAUDE  ", "claude"),  # normalized
        ("Ollama", "ollama"),
    ],
)
def test_reads_known_runtime(runtime: str, expected: str) -> None:
    contract = {"execution": {"runtime": runtime}}
    assert runtime_from_contract(contract) == expected


@pytest.mark.parametrize(
    "contract",
    [
        None,
        {},
        {"execution": {}},
        {"execution": {"runtime": "made-up-runtime"}},
        {"execution": {"runtime": ""}},
        {"execution": {"runtime": 1}},
        {"execution": None},
    ],
)
def test_absent_or_unknown_runtime_is_none(contract: object) -> None:
    assert runtime_from_contract(contract) is None  # type: ignore[arg-type]


def test_known_runtimes_contains_default_claude() -> None:
    # The default runtime must always be recognised.
    assert "claude" in KNOWN_RUNTIMES

"""#1641: shorthands point at the 5.x models; old ids stay pinnable."""

import pytest
from core.cache import _min_cacheable_tokens
from phase_config import MODEL_ID_MAP, infer_provider_from_model, thinking_config_for
from usage import estimate_cost_usd


def test_shorthands_map_to_new_ids() -> None:
    assert MODEL_ID_MAP["opus"] == "claude-opus-5-5"
    assert MODEL_ID_MAP["sonnet"] == "claude-sonnet-5-5"
    assert MODEL_ID_MAP["haiku"] == "claude-haiku-5-5"
    assert MODEL_ID_MAP["fable"] == "claude-fable-5-2"


def test_legacy_aliases_still_resolve() -> None:
    assert MODEL_ID_MAP["opus-4.8"] == "claude-opus-4-8"
    assert MODEL_ID_MAP["sonnet-4.6"] == "claude-sonnet-4-6"
    assert MODEL_ID_MAP["haiku-4.5"].startswith("claude-haiku-4-5")


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
)
def test_5x_thinking_is_adaptive_and_ignores_budget(model: str) -> None:
    assert thinking_config_for(model, "high", explicit_budget=16384) == {
        "type": "adaptive"
    }


@pytest.mark.parametrize(
    "model,provider",
    [
        ("gpt-6.2-sol", "codex"),
        ("gpt-5.6-luna", "codex"),
        ("gemini-4-carbon-medium", "gemini"),
        ("gemini-3.8-flash-high", "gemini"),
        ("fable", "claude"),
        ("claude-fable-5-2", "claude"),
    ],
)
def test_provider_detection(model: str, provider: str) -> None:
    assert infer_provider_from_model(model) == provider


def test_cost_and_cache_floor() -> None:
    assert estimate_cost_usd(1_000_000, 1_000_000, "claude-opus-5-5") == 24.0
    assert (
        estimate_cost_usd(1_000_000, 0, "gpt-6.2-sol") == 0.0
    )  # unknown price, never guessed
    assert _min_cacheable_tokens("claude-opus-5-5") == 4096

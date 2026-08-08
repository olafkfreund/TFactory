"""RFC-0014 (#461): gen_functional consumes the routed ``test_gen`` model.

``agents.gen_functional._resolve_client`` must prefer the cost-aware router's
``execution.phase_models.test_gen`` from the contract over the default
"coding"-phase model, degrade to today's behaviour when the contract has no
routed entry, and honour ``execution.runtime`` only when it disambiguates a
provider-ambiguous model (never overriding a confidently-inferred provider).

These tests exercise the pure ``_apply_runtime_override`` helper directly and
drive ``_resolve_client`` with the heavy SDK seams (``create_client`` /
``get_provider``) stubbed so no real client is constructed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.gen_functional import _apply_runtime_override, _resolve_client
from phase_config import DEFAULT_PHASE_MODELS, resolve_model_id


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "spec"
    (d / "context").mkdir(parents=True)
    return d


def _write_contract(spec_dir: Path, contract: dict) -> None:
    (spec_dir / "context" / "task_contract.json").write_text(json.dumps(contract))


# ── _apply_runtime_override ────────────────────────────────────────────────


def test_override_none_runtime_keeps_inferred() -> None:
    assert _apply_runtime_override("claude", "some-model", None) == "claude"


def test_override_disambiguates_ambiguous_model_to_ollama() -> None:
    # Model inferred to the "claude" default but is not a Claude model and the
    # runtime says ollama -> override to ollama.
    assert _apply_runtime_override("claude", "qwen3:14b", "ollama") == "ollama"


def test_override_never_touches_explicit_claude_model() -> None:
    assert _apply_runtime_override("claude", "claude-haiku-4-5", "ollama") == "claude"
    assert _apply_runtime_override("claude", "sonnet", "ollama") == "claude"


def test_override_never_downgrades_confident_provider() -> None:
    # Model string already pins ollama; an unrelated runtime can't override it.
    assert _apply_runtime_override("ollama", "ollama:qwen", "codex") == "ollama"


def test_override_ignores_gated_runtime_without_provider() -> None:
    # claude-subagents / dynamic-workflow / antigravity have no TFactory provider.
    assert _apply_runtime_override("claude", "qwen3", "claude-subagents") == "claude"
    assert _apply_runtime_override("claude", "qwen3", "dynamic-workflow") == "claude"
    assert _apply_runtime_override("claude", "qwen3", "antigravity") == "claude"


# ── _resolve_client model preference ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_client_prefers_routed_test_gen_model(
    spec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_create_client(_project_dir, _spec_dir, model, **_kwargs):
        captured["model"] = model
        return object()

    monkeypatch.setattr("core.client.create_client", _fake_create_client)
    _write_contract(
        spec_dir,
        {
            "contract_version": "2",
            "execution": {"phase_models": {"coding": "sonnet", "test_gen": "haiku"}},
        },
    )

    await _resolve_client(spec_dir, spec_dir)

    # Routed test_gen (haiku) is preferred over the coding-phase model (sonnet).
    assert captured["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_resolve_client_falls_back_without_routing(
    spec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_create_client(_project_dir, _spec_dir, model, **_kwargs):
        captured["model"] = model
        return object()

    monkeypatch.setattr("core.client.create_client", _fake_create_client)
    # No contract at all -> today's behaviour: default "coding" phase model.
    await _resolve_client(spec_dir, spec_dir)

    assert captured["model"] == resolve_model_id(DEFAULT_PHASE_MODELS["coding"])


@pytest.mark.asyncio
async def test_resolve_client_routes_ollama_via_runtime(
    spec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_get_provider(provider_name, **kwargs):
        captured["provider"] = provider_name
        captured["model"] = kwargs.get("model")
        return object()

    monkeypatch.setattr("providers.factory.get_provider", _fake_get_provider)
    _write_contract(
        spec_dir,
        {
            "contract_version": "2",
            "execution": {
                "phase_models": {"test_gen": "qwen3:14b"},
                "runtime": "ollama",
            },
        },
    )

    await _resolve_client(spec_dir, spec_dir)

    # Ambiguous model + ollama runtime -> ollama provider, routed model passed.
    assert captured["provider"] == "ollama"
    assert captured["model"] == "qwen3:14b"


# ── #870: ollama-cloud is NOT the local provider ───────────────────────────
#
# The cloud runtime is an OpenAI-compatible endpoint (https://ollama.com, key
# from OPENAI_COMPATIBLE_API_KEY / OLLAMA_API_KEY — see
# providers/ollama_cloud_check.py). Mapping it onto the local agentic ``ollama``
# provider sent a cloud-routed contract to http://localhost:11434 with no
# credentials.


def test_ollama_cloud_runtime_routes_to_openai_compatible() -> None:
    assert (
        _apply_runtime_override("claude", "qwen3:14b", "ollama-cloud")
        == "openai-compatible"
    )


def test_local_ollama_runtime_still_routes_to_the_local_provider() -> None:
    assert _apply_runtime_override("claude", "qwen3:14b", "ollama") == "ollama"


@pytest.mark.asyncio
async def test_resolve_client_routes_ollama_cloud_to_openai_compatible(
    spec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_get_provider(provider_name, **kwargs):
        captured["provider"] = provider_name
        captured["model"] = kwargs.get("model")
        captured["base_url"] = kwargs.get("base_url")
        return object()

    monkeypatch.setattr("providers.factory.get_provider", _fake_get_provider)
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://ollama.com")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-key")
    _write_contract(
        spec_dir,
        {
            "contract_version": "2",
            "execution": {
                "phase_models": {"test_gen": "qwen3:14b"},
                "runtime": "ollama-cloud",
            },
        },
    )

    await _resolve_client(spec_dir, spec_dir)

    assert captured["provider"] == "openai-compatible"
    assert captured["base_url"] == "https://ollama.com"


@pytest.mark.asyncio
async def test_resolve_client_gives_local_ollama_the_configured_base_url(
    spec_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#870: the deployment's OLLAMA_BASE_URL must reach the provider.

    The provider class defaults to http://localhost:11434 and reads no env of
    its own; inside a pod nothing listens there.
    """
    captured: dict[str, object] = {}

    def _fake_get_provider(provider_name, **kwargs):
        captured["provider"] = provider_name
        captured["base_url"] = kwargs.get("base_url")
        return object()

    monkeypatch.setattr("providers.factory.get_provider", _fake_get_provider)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.example:11434")
    _write_contract(
        spec_dir,
        {
            "contract_version": "2",
            "execution": {
                "phase_models": {"test_gen": "qwen3:14b"},
                "runtime": "ollama",
            },
        },
    )

    await _resolve_client(spec_dir, spec_dir)

    assert captured["provider"] == "ollama"
    assert captured["base_url"] == "http://ollama.example:11434"

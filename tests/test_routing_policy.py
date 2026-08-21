"""Tests for the TFACTORY_ROUTING_POLICY per-stage model router.

The verify leg was the only part of the fleet with no operator-facing model
control: verify tasks arrive via ``/api/specs/ingest``, which writes no
``task_metadata.json``, so every check in ``get_phase_model`` fell through and
``DEFAULT_PHASE_MODELS`` was the only thing deciding the model. Retargeting it
meant editing code and shipping a release — a deploy to switch a model on and
another to switch it back.

Two properties matter most here and are asserted directly: with no policy set
the resolution is byte-identical to before, and removing a policy restores the
default without a code change.
"""

from __future__ import annotations

import json
from pathlib import Path

import phase_config
import pytest
from routing_policy import ENV_VAR, load_policy, policy_route

QWEN = "openai-compatible:qwen3.8:27b"


@pytest.fixture(autouse=True)
def _clear_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may inherit a policy from the ambient environment."""
    monkeypatch.delenv(ENV_VAR, raising=False)


def _set(monkeypatch: pytest.MonkeyPatch, policy: object) -> None:
    monkeypatch.setenv(
        ENV_VAR, policy if isinstance(policy, str) else json.dumps(policy)
    )


# --------------------------------------------------------------------------
# Fail-closed: nothing about a broken policy may change routing.
# --------------------------------------------------------------------------


def test_unset_policy_is_a_noop() -> None:
    assert load_policy() is None
    assert policy_route("qa") is None


def test_empty_string_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "   ")
    assert policy_route("qa") is None


def test_malformed_json_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, "{not valid json")
    assert policy_route("qa") is None


def test_non_object_json_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, '["a", "list"]')
    assert policy_route("qa") is None


def test_unreadable_policy_file_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "/nonexistent/path/policy.json")
    assert policy_route("qa") is None


def test_unmapped_stage_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert policy_route("coding") is None


def test_stage_mapped_to_unknown_tier_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "nonexistent-tier"}})
    assert policy_route("qa") is None


# --------------------------------------------------------------------------
# Routing.
# --------------------------------------------------------------------------


def test_routes_stage_through_its_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert policy_route("qa") == (QWEN, "mid")


def test_tiers_map_may_be_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a `tiers` map the built-in DEFAULT_TIERS applies."""
    _set(monkeypatch, {"stages": {"qa": "small"}})
    model, tier = policy_route("qa")  # type: ignore[misc]
    assert (model, tier) == ("haiku", "small")


def test_policy_may_be_a_file_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Helm mounts a configmap; the env var then carries a path, not JSON."""
    doc = tmp_path / "policy.json"
    doc.write_text(json.dumps({"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}}))
    monkeypatch.setenv(ENV_VAR, str(doc))
    assert policy_route("qa") == (QWEN, "mid")


# --------------------------------------------------------------------------
# Precedence through get_phase_model — the behaviour operators actually see.
# --------------------------------------------------------------------------


def test_verify_lane_default_is_unchanged_without_a_policy(tmp_path: Path) -> None:
    """A spec dir with no task_metadata.json is the real verify-lane shape."""
    assert phase_config.get_phase_model(tmp_path, "qa") == "claude-opus-4-8"


def test_policy_retargets_the_verify_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "qa") == QWEN


def test_removing_the_policy_restores_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The point of the env var: flip back with no code change or release."""
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "qa") == QWEN
    monkeypatch.delenv(ENV_VAR)
    assert phase_config.get_phase_model(tmp_path, "qa") == "claude-opus-4-8"


def test_explicit_task_model_outranks_the_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The policy replaces the DEFAULT, never an explicit per-task choice."""
    (tmp_path / "task_metadata.json").write_text(json.dumps({"model": "haiku"}))
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "qa") == ("claude-haiku-4-5-20251001")


def test_auto_profile_phase_models_outrank_the_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "task_metadata.json").write_text(
        json.dumps({"isAutoProfile": True, "phaseModels": {"qa": "haiku"}})
    )
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "qa") == ("claude-haiku-4-5-20251001")


def test_cli_model_outranks_the_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "qa", "haiku") == (
        "claude-haiku-4-5-20251001"
    )


def test_a_stage_the_policy_does_not_map_keeps_its_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retargeting qa must not silently move coding too."""
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    assert phase_config.get_phase_model(tmp_path, "coding") == "claude-opus-4-8"


def test_the_colon_in_the_model_name_survives_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`qwen3.8:27b` carries its own colon; the whole tag must reach the provider.

    A truncated tag would be a different model, or none at all — and the run
    would still look like it tested what was asked for.
    """
    _set(monkeypatch, {"tiers": {"mid": QWEN}, "stages": {"qa": "mid"}})
    resolved = phase_config.get_phase_model(tmp_path, "qa")
    assert resolved.endswith("qwen3.8:27b"), resolved
    assert phase_config.strip_provider_prefix(resolved) == "qwen3.8:27b"

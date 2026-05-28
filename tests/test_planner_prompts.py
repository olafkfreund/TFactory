"""Tests for the TFactory Planner prompt-assembly helpers — Task 5 (#6) commit 3.

The helpers are pure-string assembly: load a `.md` file from
`apps/backend/prompts/`, prepend a context block with concrete paths.
No SDK involvement; tests stay fast and offline.

Covered:
  - get_tfactory_planner_prompt builds an initial-mode prompt
  - get_tfactory_planner_replan_prompt builds a replan-mode prompt
  - Both inject the concrete spec_dir + project_dir
  - Both raise FileNotFoundError if the .md is missing
  - The initial prompt mentions the required schema keys
  - The replan prompt warns against rewriting earlier phases
  - Output is reasonably sized (no runaway concatenation bugs)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Inherited prompts_pkg imports providers + SDK; pre-mock to stay offline.
for _m in [
    "claude_agent_sdk", "claude_agent_sdk.types",
    "core.client", "phase_config", "providers.factory",
]:
    sys.modules.setdefault(_m, MagicMock())

from prompts_pkg.prompts import (  # noqa: E402
    get_tfactory_planner_prompt,
    get_tfactory_planner_replan_prompt,
)


# ── Path injection ──────────────────────────────────────────────────────


def test_initial_includes_spec_dir() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/ws/demo/001" in p


def test_initial_includes_project_dir() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/proj" in p


def test_initial_has_spec_context_header() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "SPEC CONTEXT" in p


def test_initial_references_context_files() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "aifactory_spec.md" in p
    assert "diff.patch" in p
    assert "source.json" in p


def test_initial_points_at_test_plan_json_for_write() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "test_plan.json" in p


# ── Body content (proves the file is loaded, not just the context block) ──


def test_initial_contains_subtask_schema_keys() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    for key in ("target", "rationale", "files_to_create", "verification"):
        assert key in p, f"missing schema key in prompt: {key}"


def test_initial_warns_against_anti_patterns() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "Anti-patterns" in p or "anti-pattern" in p.lower()


def test_initial_mentions_subtask_cap() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "30" in p  # the hard cap


def test_initial_mentions_lane_functional() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "functional" in p.lower()


# ── Replan mode ─────────────────────────────────────────────────────────


def test_replan_includes_spec_dir() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/ws/demo/001" in p


def test_replan_has_replan_context_header() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "REPLAN CONTEXT" in p


def test_replan_references_replan_request_json() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "replan_request.json" in p


def test_replan_warns_against_rewriting_earlier_phases() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    # Either the explicit instruction or a strong hint must be present
    assert (
        "do not rewrite earlier phases" in p.lower()
        or "do not edit earlier phases" in p.lower()
        or "preserve every existing phase" in p.lower()
    )


def test_replan_mentions_bumping_replan_count() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "replan_count" in p


def test_replan_mentions_one_corrected_subtask() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "one corrected subtask" in p.lower() or "exactly one" in p.lower()


# ── Reasonable size + structure ──────────────────────────────────────────


def test_initial_size_in_expected_range() -> None:
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    # Body ~8 KB + context block; combined should be 5 KB – 15 KB
    assert 5000 < len(p) < 15000, f"unexpected size: {len(p)}"


def test_replan_size_in_expected_range() -> None:
    p = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert 3000 < len(p) < 10000, f"unexpected size: {len(p)}"


def test_initial_and_replan_are_distinct_prompts() -> None:
    initial = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    replan = get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert initial != replan
    assert "initial mode" in initial.lower()
    assert "replan mode" in replan.lower()


# ── Missing-file fail-fast ──────────────────────────────────────────────


def test_initial_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If planner.md is missing, FileNotFoundError surfaces clearly."""
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir
    with pytest.raises(FileNotFoundError, match="planner.md"):
        get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))


def test_replan_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="planner_replan.md"):
        get_tfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))

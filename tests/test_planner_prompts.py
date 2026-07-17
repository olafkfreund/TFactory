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
    "claude_agent_sdk",
    "claude_agent_sdk.types",
    "core.client",
    "phase_config",
    "providers.factory",
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


# =============================================================================
# Task 5 / #21 — Framework registry + catalog injection tests
# =============================================================================


def test_planner_prompt_includes_framework_registry_block() -> None:
    """Prompt must contain a ## FRAMEWORK REGISTRY block with all three frameworks."""
    p = get_tfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "## FRAMEWORK REGISTRY" in p
    assert "playwright" in p
    assert "jest" in p
    assert "pytest" in p


def test_planner_prompt_registry_block_shows_language_and_lanes() -> None:
    """Each registry entry must note language= and lanes= for the agent."""
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "language=" in p
    assert "lanes=" in p


def test_planner_prompt_includes_catalog_block_when_absent(tmp_path: Path) -> None:
    """When no catalog file exists the block says 'no catalog at this repo yet'."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()
    p = get_tfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "## TESTS CATALOG" in p
    assert "no catalog at this repo yet" in p


def test_planner_prompt_includes_catalog_block_when_present(tmp_path: Path) -> None:
    """When a catalog file exists the block lists entries with test_id and covers_acs."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "ac1-login-flow",
                "test_file": "tests/e2e/login.spec.ts",
                "framework": "playwright",
                "lane": "browser",
                "language": "typescript",
                "covers_acs": ["AC#1: User can log in"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "001",
                "last_verdict": "accept",
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_tfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "## TESTS CATALOG" in p
    assert "ac1-login-flow" in p
    assert "covers_acs" in p or "AC#1: User can log in" in p


def test_planner_prompt_catalog_block_shows_framework_and_lane(tmp_path: Path) -> None:
    """Catalog block lines include framework= and lane= for the agent."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "tc-1",
                "test_file": "tests/test_foo.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#3: returns 200"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "002",
                "last_verdict": "accept",
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_tfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "framework=pytest" in p
    assert "lane=unit" in p


def test_planner_prompt_catalog_block_shows_total_count(tmp_path: Path) -> None:
    """Catalog block footer line reports total entry count."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    tests = []
    for i in range(3):
        tests.append(
            {
                "test_id": f"tc-{i}",
                "test_file": f"tests/test_{i}.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": [f"AC#{i}"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "003",
                "last_verdict": "accept",
            }
        )
    (spec_dir / "context" / "tests_catalog.json").write_text(
        json.dumps({"version": 1, "updated_at": "2026-05-28T00:00:00Z", "tests": tests})
    )

    p = get_tfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "3 entries total" in p


def test_planner_prompt_catalog_block_flags_operator_locked(tmp_path: Path) -> None:
    """Locked catalog entries must be marked [operator_locked] in the block."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "locked-test",
                "test_file": "tests/locked.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#5: locked"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "004",
                "last_verdict": "accept",
                "operator_locked": True,
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_tfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "operator_locked" in p


def test_planner_prompt_mentions_intent_create_update_skip() -> None:
    """Prompt body must mention all three intent values."""
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "intent" in p
    assert "create" in p
    assert "update" in p
    assert "skip" in p


def test_planner_prompt_mentions_tfactory_yml_and_catalog_context_files() -> None:
    """SPEC CONTEXT block must list both new v0.2 context files."""
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "tfactory_yml.json" in p
    assert "tests_catalog.json" in p


# =============================================================================
# #443 — deterministic language detection (no more pytest-by-default for Go)
# =============================================================================

from prompts_pkg.prompts import _build_detected_language_block  # noqa: E402


def _spec_with_ac(tmp_path: Path, ac_text: str) -> Path:
    spec_dir = tmp_path / "spec"
    (spec_dir / "context").mkdir(parents=True)
    (spec_dir / "context" / "aifactory_spec.md").write_text(ac_text)
    return spec_dir


def test_detect_go_from_ac_commands(tmp_path: Path) -> None:
    """`go test` in the AC text pins the project to (go, go-test), not pytest."""
    spec_dir = _spec_with_ac(tmp_path, "## AC\n- AC#6: `go test ./...` passes.")
    block = _build_detected_language_block(spec_dir, tmp_path / "proj")
    assert "DETECTED PROJECT LANGUAGE" in block
    assert "**go** project" in block
    assert "language: go" in block
    assert "framework: go-test" in block
    assert "Do NOT emit pytest" in block


def test_ac_command_wins_over_ambiguous_manifests(tmp_path: Path) -> None:
    """A go.mod + pyproject.toml repo still routes to Go when the AC says `go test`."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module hello\n")
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    spec_dir = _spec_with_ac(tmp_path, "Verify with `go test ./...`.")
    block = _build_detected_language_block(spec_dir, proj)
    assert "**go** project" in block
    assert "language: go" in block
    # the Python manifest is reported as corroboration, never as the choice
    assert "pyproject.toml" in block


def test_detect_python_has_no_pytest_warning(tmp_path: Path) -> None:
    """A pytest AC pins Python and omits the 'do NOT emit pytest' warning."""
    spec_dir = _spec_with_ac(tmp_path, "- run `pytest tests/`")
    block = _build_detected_language_block(spec_dir, tmp_path / "proj")
    assert "**python** project" in block
    assert "language: python" in block
    assert "Do NOT emit pytest" not in block


def test_detect_from_manifest_only_when_unambiguous(tmp_path: Path) -> None:
    """No AC command but a lone go.mod → Go by manifest."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module hello\n")
    spec_dir = _spec_with_ac(tmp_path, "Greet the user politely.")
    block = _build_detected_language_block(spec_dir, proj)
    assert "**go** project" in block


def test_no_signal_emits_detect_via_glob_guidance(tmp_path: Path) -> None:
    """No AC command and no manifest → tell the agent to detect, never assume pytest."""
    spec_dir = _spec_with_ac(tmp_path, "Greet the user politely.")
    block = _build_detected_language_block(spec_dir, tmp_path / "proj")
    assert "No deterministic language signal" in block
    assert "never assume pytest" in block


def test_full_prompt_includes_detected_language_block(tmp_path: Path) -> None:
    """get_tfactory_planner_prompt threads the detected-language block in."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module hello\n")
    spec_dir = _spec_with_ac(tmp_path, "- AC#6: `go test ./...` passes.")
    p = get_tfactory_planner_prompt(spec_dir, proj)
    assert "## DETECTED PROJECT LANGUAGE" in p
    assert "framework: go-test" in p


# =============================================================================
# #696 — lane follows the deliverable, not repo-global markers
# =============================================================================

import json  # noqa: E402
import subprocess  # noqa: E402


def _git_repo_with_branch_diff(
    tmp_path: Path, base_files: dict[str, str], branch_files: dict[str, str]
) -> Path:
    """Build a repo whose default branch has ``base_files`` and whose checked-out
    (detached) HEAD adds ``branch_files`` — mirroring the spec-ingest checkout."""
    proj = tmp_path / "proj"
    proj.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(proj), *args], check=True, capture_output=True)

    _git("init", "-b", "main")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    for name, text in base_files.items():
        p = proj / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git("add", "-A")
    _git("commit", "-m", "base")
    # Simulate the clone's remote-tracking default branch.
    _git("update-ref", "refs/remotes/origin/main", "main")
    for name, text in branch_files.items():
        p = proj / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git("add", "-A")
    _git("commit", "-m", "build")
    # Detached HEAD, like _checkout_source_branch's FETCH_HEAD checkout.
    _git("checkout", "--detach")
    return proj


def _spec_with_source_branch(tmp_path: Path, ac_text: str) -> Path:
    spec_dir = _spec_with_ac(tmp_path, ac_text)
    (spec_dir / "context" / "source.json").write_text(
        json.dumps({"mode": "spec_ingest", "source_branch": "feat/build"})
    )
    return spec_dir


def test_python_diff_wins_over_go_repo_markers(tmp_path: Path) -> None:
    """#696 repro: go.mod repo + pure-Python source-branch diff → Python lane."""
    proj = _git_repo_with_branch_diff(
        tmp_path,
        base_files={"go.mod": "module hello\n", "main.go": "package main\n"},
        branch_files={
            "helpers/roman.py": "def roman(n): ...\n",
            "tests/test_roman.py": "def test_roman(): ...\n",
        },
    )
    spec_dir = _spec_with_source_branch(tmp_path, "Deliver the helpers politely.")
    block = _build_detected_language_block(spec_dir, proj)
    assert "**python** project" in block
    assert "language: python" in block
    assert "source-branch diff" in block


def test_go_diff_in_python_repo_selects_go(tmp_path: Path) -> None:
    """Polyglot ladder reverse: pyproject repo + pure-Go diff → Go lane."""
    proj = _git_repo_with_branch_diff(
        tmp_path,
        base_files={"pyproject.toml": "[project]\nname='x'\n"},
        branch_files={"greet.go": "package main\n", "greet_test.go": "package main\n"},
    )
    spec_dir = _spec_with_source_branch(tmp_path, "Greet the user politely.")
    block = _build_detected_language_block(spec_dir, proj)
    assert "**go** project" in block
    assert "language: go" in block


def test_spec_named_py_files_win_over_mixed_manifests(tmp_path: Path) -> None:
    """No branch diff, mixed go.mod+pyproject repo, AC names helpers/*.py → Python."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module hello\n")
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    spec_dir = _spec_with_ac(
        tmp_path,
        "## AC\n- AC#1: helpers/roman.py converts integers.\n"
        "- AC#2: tests/test_roman.py passes.\n",
    )
    block = _build_detected_language_block(spec_dir, proj)
    assert "**python** project" in block
    assert "language: python" in block


def test_diff_patch_fallback_selects_language(tmp_path: Path) -> None:
    """No git checkout, but snapshotter diff.patch is pure Python → Python lane."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module hello\n")
    spec_dir = _spec_with_ac(tmp_path, "Deliver the helpers politely.")
    (spec_dir / "context" / "diff.patch").write_text(
        "--- a/helpers/roman.py\n+++ b/helpers/roman.py\n"
        "--- a/tests/test_roman.py\n+++ b/tests/test_roman.py\n"
    )
    block = _build_detected_language_block(spec_dir, proj)
    assert "**python** project" in block


def test_planner_body_offers_go_in_schema_and_registry() -> None:
    """planner.md now lists Go as a language option and go-test as a framework."""
    p = get_tfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "go" in p and "go-test" in p
    # the framework registry block enumerates the real go-test descriptor
    assert "go-test: language=go" in p

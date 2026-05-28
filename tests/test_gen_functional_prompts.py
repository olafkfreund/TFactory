"""Tests for the Gen-Functional prompt assembly helper — Task 6 (#7) commit 4.

The helper assembles a per-subtask system prompt by loading
``apps/backend/prompts/gen_functional.md`` and prepending a SUBTASK
CONTEXT block. Covered:

  - Path injection (spec_dir, project_dir, files_to_create resolved
    against spec_dir for the Write target)
  - Subtask-field injection (id, description, target, rationale,
    verification command)
  - Both shapes: Subtask dataclass AND raw dict (post-JSON-load shape)
  - Verification command discovery handles both schema variants
    (``run`` from the dataclass, ``command`` from the planner prompt)
  - FileNotFoundError when gen_functional.md is missing
  - Body content sanity (mentions key rules + tools + anti-patterns)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prompts_pkg.prompts import get_tfactory_gen_functional_prompt
from test_plan import Lane, Subtask, Verification, VerificationType


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def subtask_dataclass() -> Subtask:
    return Subtask(
        id="ac1-login-expiry",
        description="Verify login_user sets expires_at to +24h",
        lane=Lane.UNIT,
        target="app/auth/login.py::login_user",
        rationale="AC#1: login_user returns session with expires_at=+24h",
        files_to_create=["tests/test_login_expiry.py"],
        verification=Verification(
            type=VerificationType.COMMAND,
            run="pytest tests/test_login_expiry.py",
        ),
    )


@pytest.fixture
def subtask_dict() -> dict:
    """The shape that ImplementationPlan.load() produces from JSON."""
    return {
        "id": "ac1-login-expiry",
        "description": "Verify login_user sets expires_at to +24h",
        "lane": "functional",
        "target": "app/auth/login.py::login_user",
        "rationale": "AC#1: login_user returns session with expires_at=+24h",
        "files_to_create": ["tests/test_login_expiry.py"],
        "verification": {
            "type": "command",
            "command": "pytest tests/test_login_expiry.py",
        },
    }


# ── Path + field injection ──────────────────────────────────────────────


def test_includes_spec_dir(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dataclass,
    )
    assert "/ws/demo/001" in p


def test_includes_project_dir(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dataclass,
    )
    assert "/proj" in p


def test_includes_subtask_id_and_description(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "ac1-login-expiry" in p
    assert "expires_at" in p  # from description


def test_includes_target(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "app/auth/login.py::login_user" in p


def test_includes_rationale(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/x"), Path("/p"), subtask_dataclass,
    )
    assert "AC#1" in p


def test_write_path_resolves_against_spec_dir(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/p"), subtask_dataclass,
    )
    # The agent should see the full absolute path it must Write to.
    assert "/ws/demo/001/tests/test_login_expiry.py" in p


def test_includes_verification_command_from_dataclass(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    assert "pytest tests/test_login_expiry.py" in p


# ── Dict shape (post-JSON-load) ─────────────────────────────────────────


def test_accepts_dict_shape(subtask_dict: dict) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws/demo/001"), Path("/proj"), subtask_dict,
    )
    assert "ac1-login-expiry" in p
    assert "app/auth/login.py::login_user" in p


def test_dict_verification_uses_command_key(subtask_dict: dict) -> None:
    """Planner emits ``"command"`` in JSON; helper finds it."""
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dict,
    )
    assert "pytest tests/test_login_expiry.py" in p


def test_dict_verification_falls_back_to_run_key() -> None:
    """The Verification.to_dict() shape uses ``run`` — helper supports that too."""
    sd = {
        "id": "x", "description": "y", "lane": "functional",
        "target": "f.py::g", "rationale": "AC#X",
        "files_to_create": ["tests/x.py"],
        "verification": {"type": "command", "run": "pytest tests/x.py"},
    }
    p = get_tfactory_gen_functional_prompt(Path("/w"), Path("/p"), sd)
    assert "pytest tests/x.py" in p


# ── Body content sanity ────────────────────────────────────────────────


def test_mentions_guardrails(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # The prompt body should describe the two guardrails by name.
    assert "Pre-flight" in p or "pre-flight" in p.lower()
    assert "Flake-risk" in p or "flake" in p.lower()


def test_mentions_tool_grants(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    for tool in ("Read", "Write", "Glob", "Grep"):
        assert tool in p
    # Negative: no Bash / Edit
    assert "Bash" in p  # mentioned in the "do NOT have" list
    assert "do NOT have" in p or "no bash" in p.lower()


def test_mentions_all_five_flake_patterns(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # The flake-lint patterns the agent must avoid.
    assert "dict" in p.lower()
    assert "set" in p.lower()
    assert "random" in p.lower()
    assert "time.sleep" in p
    assert "datetime.now" in p or "datetime.now()" in p


def test_lists_anti_patterns(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    assert "Anti-patterns" in p or "anti-pattern" in p.lower()


# ── Size sanity ────────────────────────────────────────────────────────


def test_total_size_in_range(subtask_dataclass: Subtask) -> None:
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dataclass,
    )
    # Body ~7KB + context block (~1KB). Combined: 5-15 KB.
    assert 5_000 < len(p) < 15_000, f"unexpected size {len(p)}"


# ── Failure mode ───────────────────────────────────────────────────────


def test_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subtask_dataclass: Subtask,
) -> None:
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir
    with pytest.raises(FileNotFoundError, match="gen_functional.md"):
        get_tfactory_gen_functional_prompt(
            Path("/ws"), Path("/p"), subtask_dataclass,
        )


def test_handles_missing_files_to_create_gracefully(subtask_dict: dict) -> None:
    """If the planner emits a subtask with no files_to_create, we degrade
    to a "?" placeholder rather than crashing."""
    subtask_dict["files_to_create"] = []
    p = get_tfactory_gen_functional_prompt(
        Path("/ws"), Path("/p"), subtask_dict,
    )
    # Falls back to "?"; agent will surface the issue rather than write
    # to a real-looking but wrong path.
    assert "write the file at:" in p

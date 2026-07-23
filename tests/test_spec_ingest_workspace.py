#!/usr/bin/env python3
"""Tests for the WS2 generic-ingestion seam — create_spec_ingest_workspace.

Verifies the no-AIFactory front door: a raw markdown / Gherkin / EARS spec
becomes a TFactory workspace (context/aifactory_spec.md + target-mode
source.json + status.json), with parse-before-create failure semantics.
Planner scheduling is disabled (schedule=False) so no SDK is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.tools_pkg.tools.task_control import (
    create_spec_ingest_workspace,  # noqa: E402
)

_GHERKIN = """Feature: Login
  Scenario: Successful login
    Given a registered user
    When they submit valid credentials
    Then a session is created
  Scenario: Bad password
    When they submit a wrong password
    Then login is rejected
"""

_MARKDOWN = """# Checkout

Some context.

## Acceptance Criteria
- Cart totals include tax
- Empty cart cannot check out
"""

_EARS = """The system shall reject expired coupons.
When the cart is empty, the system shall disable checkout.
"""


def _ingest(tmp_path: Path, text: str, **kw):
    return create_spec_ingest_workspace(
        project_id="proj",
        spec_id=kw.pop("spec_id", "spec1"),
        spec_text=text,
        root=tmp_path,
        schedule=False,
        **kw,
    )


def _spec_dir(tmp_path: Path, spec_id="spec1") -> Path:
    return tmp_path / "workspaces" / "proj" / "specs" / spec_id


# ─── happy paths per format ───────────────────────────────────────────────


def _git(cwd, *args):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )


def test_unresolvable_source_branch_fails_spec_loudly(tmp_path):
    """A named build branch that can't be checked out fails the spec loudly rather
    than proceeding on whatever tree is checked out — silently verifying the wrong
    code is worse than not verifying (#742/#96). source_branch is still recorded
    for provenance, and the planner is not scheduled."""
    result = _ingest(
        tmp_path,
        _MARKDOWN,
        target_paths=["src/x.py"],
        project_root=str(tmp_path / "not-a-repo"),
        source_branch="aifactory/123",
    )
    assert result["source_format"] == "markdown"
    assert result["status"] == "failed"
    assert result["planner_scheduled"] is False
    assert any(
        "source_branch" in w or "not a git repo" in w for w in result["warnings"]
    )
    status = json.loads((_spec_dir(tmp_path) / "status.json").read_text())
    assert status["status"] == "failed"
    assert status["phase"] == "source_checkout_failed"
    assert status["source_checkout_error"]
    # source_branch is still recorded even on failure (provenance / re-drive).
    source = json.loads((_spec_dir(tmp_path) / "context" / "source.json").read_text())
    assert source["source_branch"] == "aifactory/123"


def _origin_with_build(tmp_path, *, name="origin"):
    """Build a bare-ish origin with a base branch plus a build branch that adds
    built.rs. Returns (origin_path, base_branch)."""
    origin = tmp_path / name
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("base")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "base")
    base_branch = (
        _git(origin, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    )
    _git(origin, "checkout", "-qb", "aifactory/999")
    (origin / "built.rs").write_text('fn greet() -> &str { "Hello" }')
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "build")
    _git(origin, "checkout", "-q", base_branch)
    return origin, base_branch


def test_source_branch_materializes_worktree_not_shared_clone(tmp_path):
    """The build branch is materialized as the spec's OWN git worktree (#742), so
    the SUT is the actual built code (#96) WITHOUT touching the shared clone —
    concurrent specs can't clobber each other's HEAD."""
    import json as _json
    import subprocess

    origin, _ = _origin_with_build(tmp_path)
    # project_root = a shared clone of origin, on the base branch (NO built.rs).
    proj = tmp_path / "proj-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(proj)], capture_output=True)
    assert not (proj / "built.rs").exists()

    _ingest(
        tmp_path,
        _MARKDOWN,
        target_paths=["built.rs"],
        project_root=str(proj),
        source_branch="aifactory/999",
    )

    sd = _spec_dir(tmp_path)
    source = _json.loads((sd / "context" / "source.json").read_text())
    status = _json.loads((sd / "status.json").read_text())
    assert source["worktree"] is not None, (
        f"worktree not created: {status.get('source_checkout_error')}"
    )
    worktree = Path(source["worktree"])
    # The spec's worktree holds the build...
    assert worktree == sd / ".worktree"
    assert (worktree / "built.rs").exists()
    assert "Hello" in (worktree / "built.rs").read_text()
    assert source["source_sha"]
    # ...and the shared clone's working tree is UNTOUCHED (still on base branch).
    assert not (proj / "built.rs").exists()


def test_concurrent_specs_get_independent_worktrees(tmp_path):
    """Two specs off the SAME shared clone get independent worktrees — the core
    #742 property. Under the old in-place checkout, the second ingest moved the
    shared HEAD and the first spec's tree changed under it."""
    import subprocess

    origin, base_branch = _origin_with_build(tmp_path)
    # A second build branch with different content.
    _git(origin, "checkout", "-q", "aifactory/999")
    _git(origin, "checkout", "-qb", "aifactory/998")
    (origin / "built.rs").write_text('fn greet() -> &str { "Goodbye" }')
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "build2")
    _git(origin, "checkout", "-q", base_branch)

    proj = tmp_path / "proj-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(proj)], capture_output=True)

    _ingest(
        tmp_path,
        _MARKDOWN,
        spec_id="spec-a",
        target_paths=["built.rs"],
        project_root=str(proj),
        source_branch="aifactory/999",
    )
    _ingest(
        tmp_path,
        _MARKDOWN,
        spec_id="spec-b",
        target_paths=["built.rs"],
        project_root=str(proj),
        source_branch="aifactory/998",
    )

    wt_a = _spec_dir(tmp_path, "spec-a") / ".worktree" / "built.rs"
    wt_b = _spec_dir(tmp_path, "spec-b") / ".worktree" / "built.rs"
    # spec-a still sees its own build after spec-b was ingested — no clobber.
    assert "Hello" in wt_a.read_text()
    assert "Goodbye" in wt_b.read_text()


def test_gherkin_creates_workspace(tmp_path):
    result = _ingest(tmp_path, _GHERKIN, target_paths=["src/auth.py"])
    assert result["source_format"] == "gherkin"
    assert result["ac_count"] >= 1
    assert result["planner_scheduled"] is False

    sd = _spec_dir(tmp_path)
    spec_md = (sd / "context" / "aifactory_spec.md").read_text()
    assert "## Acceptance Criteria" in spec_md
    assert "AC#1" in spec_md

    source = json.loads((sd / "context" / "source.json").read_text())
    assert source["mode"] == "spec_ingest"
    assert source["source_format"] == "gherkin"
    assert source["target_paths"] == ["src/auth.py"]

    status = json.loads((sd / "status.json").read_text())
    assert status["mode"] == "spec_ingest" and status["status"] == "pending"


def test_markdown_format(tmp_path):
    result = _ingest(tmp_path, _MARKDOWN)
    assert result["source_format"] == "markdown"
    assert result["ac_count"] == 2


def test_ears_format(tmp_path):
    result = _ingest(tmp_path, _EARS)
    assert result["source_format"] == "ears"
    assert result["ac_count"] >= 1


def test_format_override_respected(tmp_path):
    # Force markdown parsing on Gherkin-looking text.
    result = _ingest(tmp_path, _MARKDOWN, fmt="markdown")
    assert result["source_format"] == "markdown"


# ─── failure semantics ────────────────────────────────────────────────────


def test_tenant_stamped_default(tmp_path):
    """No tenant supplied — 'default' is stamped into both stores (#683)."""
    _ingest(tmp_path, _MARKDOWN)
    sd = _spec_dir(tmp_path)
    source = json.loads((sd / "context" / "source.json").read_text())
    status = json.loads((sd / "status.json").read_text())
    assert source["tenant"] == "default"
    assert status["tenant"] == "default"


def test_tenant_stamped_explicit(tmp_path):
    """An explicit tenant rides into source.json + status.json (#683)."""
    _ingest(tmp_path, _MARKDOWN, tenant="acme")
    sd = _spec_dir(tmp_path)
    source = json.loads((sd / "context" / "source.json").read_text())
    status = json.loads((sd / "status.json").read_text())
    assert source["tenant"] == "acme"
    assert status["tenant"] == "acme"


def test_no_criteria_raises_and_leaves_no_dir(tmp_path):
    with pytest.raises(ValueError):
        _ingest(tmp_path, "# Title only\n\njust prose, no criteria\n")
    # parse-before-create: nothing was left behind
    assert not _spec_dir(tmp_path).exists()


def test_existing_spec_dir_raises(tmp_path):
    _ingest(tmp_path, _MARKDOWN)
    with pytest.raises(FileExistsError):
        _ingest(tmp_path, _MARKDOWN)  # same spec_id → collision


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ─── #71 Phase 3: contract-carrying handoff ────────────────────────────────


def test_contract_persisted_for_authoritative_profile(tmp_path):
    # A signed Task Contract on the handoff is written to
    # context/task_contract.json — exactly where read_task_contract() looks
    # first — so the Planner uses the DECLARED tfactory profile, not inference.
    from agents.task_contract import parse_tfactory_profile, read_task_contract

    contract = {
        "feature": "GET /api/health",
        "contract_version": "2",
        "tfactory": {
            "lanes": ["unit", "api"],
            "frameworks": {"unit": "pytest", "api": "pytest"},
            "ac_to_code_map": {"AC1": ["app/main.py"]},
        },
    }
    _ingest(tmp_path, _MARKDOWN, target_paths=["app/main.py"], contract=contract)
    sd = _spec_dir(tmp_path)

    assert (sd / "context" / "task_contract.json").exists()
    got = read_task_contract(sd)
    assert got is not None and "tfactory" in got
    prof = parse_tfactory_profile(got)
    assert prof is not None
    assert prof.lanes == ("unit", "api")


def test_contract_phase_models_written_to_task_metadata(tmp_path):
    # The handoff contract's execution.phase_models (the build's model choice,
    # e.g. Ollama) is translated into spec_dir/task_metadata.json so the verify
    # lanes (evaluator/planner/qa via get_phase_model) run on the same provider
    # instead of TFactory's default. Only the get_phase_model keys are kept.
    import json as _json

    contract = {
        "contract_version": "2",
        "execution": {
            "phase_models": {
                "spec": "openai-compatible:gpt-oss:120b",
                "planning": "openai-compatible:gpt-oss:120b",
                "coding": "openai-compatible:gpt-oss:120b",
                "qa": "openai-compatible:gpt-oss:120b",
                "qa_fixer": "openai-compatible:gpt-oss:120b",
                "test_gen": "openai-compatible:gpt-oss:120b",
            }
        },
    }
    _ingest(tmp_path, _MARKDOWN, target_paths=["app/main.py"], contract=contract)
    meta_path = _spec_dir(tmp_path) / "task_metadata.json"
    assert meta_path.exists()
    meta = _json.loads(meta_path.read_text())
    assert meta["isAutoProfile"] is True
    assert set(meta["phaseModels"]) == {"spec", "planning", "coding", "qa", "qa_fixer"}
    assert meta["phaseModels"]["coding"] == "openai-compatible:gpt-oss:120b"


def test_no_phase_models_no_task_metadata(tmp_path):
    # A contract without execution.phase_models writes no task_metadata.json
    # (verify keeps its default model resolution).
    _ingest(
        tmp_path,
        _MARKDOWN,
        target_paths=["app/main.py"],
        contract={"contract_version": "2", "tfactory": {"lanes": ["unit"]}},
    )
    assert not (_spec_dir(tmp_path) / "task_metadata.json").exists()


def test_no_contract_means_inference(tmp_path):
    # No contract (or one without RFC-0002 markers) → no task_contract.json,
    # so TFactory falls back to inferring tests from the spec. Backward compatible.
    from agents.task_contract import read_task_contract

    _ingest(tmp_path, _MARKDOWN, target_paths=["app/main.py"])
    sd = _spec_dir(tmp_path)
    assert not (sd / "context" / "task_contract.json").exists()
    assert read_task_contract(sd) is None

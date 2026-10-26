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


def test_ingested_spec_body_is_on_disk_verbatim(tmp_path):
    """#855: the body was discarded at ingest and unrecoverable from the workspace."""
    text = (
        "## Slug rules\n\n"
        "- truncate to 200 characters BEFORE slugging, never after\n\n"
        "## Acceptance Criteria\n\n"
        "- AC#1: GET /healthz returns 200\n"
    )
    _ingest(tmp_path, text, spec_id="body-spec")
    ctx = _spec_dir(tmp_path, "body-spec") / "context"
    on_disk = (ctx / "aifactory_spec.md").read_text() + (
        ctx / "source.json"
    ).read_text()
    assert "BEFORE slugging" in on_disk


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
        **_VALID_CONTRACT,
        "feature": "GET /api/health",
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
        **_VALID_CONTRACT,
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
        contract=_VALID_CONTRACT,
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


# ─── #1404: target-mode verifies a fetched, recorded ref ──────────────────


def _stale_shared_clone(tmp_path):
    """origin on its default branch (with fresh.txt) and a shared clone left on a
    leftover task branch that predates it — the #1404 live shape."""
    import subprocess

    origin, base = _origin_with_build(tmp_path)
    (origin / "fresh.txt").write_text("default head")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "fresh")
    proj = tmp_path / "proj-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(proj)], capture_output=True)
    _git(proj, "checkout", "-q", "aifactory/999")
    head = _git(origin, "rev-parse", base).stdout.strip()
    return origin, proj, base, head


def _shared_state(proj):
    return (
        _git(proj, "rev-parse", "HEAD").stdout,
        _git(proj, "rev-parse", "--abbrev-ref", "HEAD").stdout,
        _git(proj, "status", "--porcelain").stdout,
    )


def test_stale_checkout_verifies_default_branch_head(tmp_path):
    _, proj, base, head = _stale_shared_clone(tmp_path)
    before = _shared_state(proj)

    result = _ingest(tmp_path, _MARKDOWN, project_root=str(proj))

    sd = _spec_dir(tmp_path)
    source = json.loads((sd / "context" / "source.json").read_text())
    status = json.loads((sd / "status.json").read_text())
    assert result["status"] == "pending", result["warnings"]
    assert source["worktree"] == str(sd / ".worktree")
    assert (sd / ".worktree" / "fresh.txt").exists()
    assert source["source_ref"] == status["source_ref"] == base
    assert source["source_sha"] == status["source_sha"] == head
    # The shared checkout is untouched: same HEAD, same branch, clean.
    assert _shared_state(proj) == before


def test_explicit_ref_branch_and_commit_honoured(tmp_path):
    origin, proj, _, _ = _stale_shared_clone(tmp_path)
    build = _git(origin, "rev-parse", "aifactory/999").stdout.strip()
    # Annotated tag: its object sha differs from the commit; must peel (#1414).
    _git(origin, "tag", "-a", "v1.2.0", "-m", "rel", build)
    before = _shared_state(proj)

    for spec_id, ref in (
        ("by-branch", "aifactory/999"),
        ("by-sha", build),
        ("by-annotated-tag", "v1.2.0"),
    ):
        _ingest(tmp_path, _MARKDOWN, spec_id=spec_id, project_root=str(proj), ref=ref)
        sd = _spec_dir(tmp_path, spec_id)
        status = json.loads((sd / "status.json").read_text())
        assert status["source_ref"] == ref
        assert status["source_sha"] == build, status.get("source_checkout_error")
        assert not (sd / ".worktree" / "fresh.txt").exists()
    assert _shared_state(proj) == before


def test_bad_ref_fails_not_run_environment(tmp_path):
    _, proj, _, _ = _stale_shared_clone(tmp_path)
    before = _shared_state(proj)

    result = _ingest(tmp_path, _MARKDOWN, project_root=str(proj), ref="no/such-ref")

    status = json.loads((_spec_dir(tmp_path) / "status.json").read_text())
    assert result["status"] == "failed"
    assert result["planner_scheduled"] is False
    assert status["verify"] == "not_run"
    assert status["failure_kind"] == "environment"
    assert "no/such-ref" in status["source_checkout_error"]
    assert _shared_state(proj) == before


def test_no_origin_fails_rather_than_verifying_on_disk(tmp_path):
    proj = tmp_path / "local"
    proj.mkdir()
    _git(proj, "init", "-q")
    result = _ingest(tmp_path, _MARKDOWN, project_root=str(proj))
    status = json.loads((_spec_dir(tmp_path) / "status.json").read_text())
    assert result["status"] == "failed"
    assert status["failure_kind"] == "environment"
    assert "default branch" in status["source_checkout_error"]


def test_option_shaped_ref_rejected_before_workspace(tmp_path):
    with pytest.raises(ValueError, match="invalid ref"):
        _ingest(tmp_path, _MARKDOWN, ref="--upload-pack=touch /tmp/x")
    assert not _spec_dir(tmp_path).exists()


def test_target_mode_worktree_reclaimed_when_terminal(tmp_path):
    from agents.liveness_sweep import gc_terminal_worktrees

    _, proj, _, _ = _stale_shared_clone(tmp_path)
    _ingest(tmp_path, _MARKDOWN, project_root=str(proj))
    sd = _spec_dir(tmp_path)
    status = json.loads((sd / "status.json").read_text())
    status["status"] = "triaged"
    (sd / "status.json").write_text(json.dumps(status))

    assert gc_terminal_worktrees(tmp_path) == [sd]
    assert not (sd / ".worktree").exists()


def test_gc_deletes_own_spec_ref_keeps_others(tmp_path):
    """#1416: reclaiming a terminal spec's worktree also deletes its fetch ref
    (so its objects can be collected), and never another spec's ref."""
    from agents.liveness_sweep import gc_terminal_worktrees

    _, proj, _, _ = _stale_shared_clone(tmp_path)
    _ingest(tmp_path, _MARKDOWN, project_root=str(proj), spec_id="done")
    _ingest(tmp_path, _MARKDOWN, project_root=str(proj), spec_id="live")
    sd = _spec_dir(tmp_path, "done")
    status = json.loads((sd / "status.json").read_text())
    status["status"] = "triaged"
    (sd / "status.json").write_text(json.dumps(status))

    def has_ref(spec_id):
        ref = f"refs/tfactory/specs/{spec_id}"
        return _git(proj, "rev-parse", "--verify", "-q", ref).returncode == 0

    assert has_ref("done") and has_ref("live")
    assert gc_terminal_worktrees(tmp_path) == [sd]
    assert not has_ref("done")
    assert has_ref("live")


def test_ref_with_source_branch_rejected_before_workspace(tmp_path):
    with pytest.raises(ValueError, match="either ref or source_branch"):
        _ingest(tmp_path, _MARKDOWN, ref="main", source_branch="aifactory/1")
    assert not _spec_dir(tmp_path).exists()


# --- #1427: contract schema gate at ingest ---------------------------------

_VALID_CONTRACT = {
    "contract_version": "2",
    "feature": "f",
    "workflow_type": "feature",
    "phases": [
        {
            "phase": 1,
            "name": "p",
            "subtasks": [{"id": "1.1", "description": "d", "status": "pending"}],
        }
    ],
    "tfactory": {"lanes": ["unit"]},
}
_MODE = "TFACTORY_CONTRACT_SCHEMA_MODE"


def _invalid_contract():
    # wrong type, so the error path names the field ("<root>: required" would not)
    return {**_VALID_CONTRACT, "phases": "x"}


def test_valid_contract_passes_in_enforce_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(_MODE, "enforce")
    result = _ingest(tmp_path, _MARKDOWN, contract=_VALID_CONTRACT)
    assert (_spec_dir(tmp_path) / "context" / "task_contract.json").exists()
    assert not any(w.startswith("task contract fails schema") for w in result["warnings"])


def test_invalid_contract_rejected_in_enforce_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(_MODE, "enforce")
    with pytest.raises(ValueError, match="phases"):
        _ingest(tmp_path, _MARKDOWN, contract=_invalid_contract())
    assert not _spec_dir(tmp_path).exists()


def test_invalid_contract_rejected_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(_MODE, raising=False)
    with pytest.raises(ValueError, match="phases"):
        _ingest(tmp_path, _MARKDOWN, contract=_invalid_contract())
    assert not _spec_dir(tmp_path).exists()


def test_invalid_contract_warned_in_warn_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(_MODE, "warn")
    result = _ingest(tmp_path, _MARKDOWN, contract=_invalid_contract())
    assert (_spec_dir(tmp_path) / "context" / "task_contract.json").exists()
    assert any("phases" in w for w in result["warnings"])


def test_unknown_mode_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(_MODE, "enforced")
    with pytest.raises(ValueError, match="phases"):
        _ingest(tmp_path, _MARKDOWN, contract=_invalid_contract())


def test_enforce_without_contract_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(_MODE, "enforce")
    _ingest(tmp_path, _MARKDOWN)
    assert not (_spec_dir(tmp_path) / "context" / "task_contract.json").exists()


def test_validate_returns_paths_not_values():
    from agents.task_contract import validate_task_contract

    errors = validate_task_contract({"contract_version": "SECRET"})
    assert "contract_version: enum" in errors
    assert not any("SECRET" in e for e in errors)

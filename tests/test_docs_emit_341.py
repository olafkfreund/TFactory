"""Tests for the vendored docs-emit core + render_test_results (#341).

Covers the acceptance criteria:
  - render_test_results(triage) → DocBundle with the plan's correlation_key
    and generated_by="tfactory";
  - emit_bundle publishes to the repo target and the doc resolves by the same
    correlation_key (the non-plan DocBundle round-trip);
  - the Triager hook (maybe_emit_docs) is gated behind TFACTORY_DOCS_EMIT and
    is best-effort.
"""

from __future__ import annotations

import json
from pathlib import Path

from emit.docs import (
    DocBundle,
    PlanDocsResolver,
    emit_bundle,
    render_test_results,
)
from emit.docs.targets.repo import RepoDocsTarget

TRIAGE = {
    "mode": "real",
    "component_ref": "component:default/widget-svc",
    "summary": {
        "committed_count": 3,
        "flagged_count": 1,
        "rejected_count": 1,
        "skipped_count": 0,
    },
    "committed": [
        {
            "test_id": "test_a",
            "test_file": "tests/test_a.py",
            "verdict": {
                "verdict": "accept",
                "signals_summary": {
                    "coverage_delta_pct": 4.2,
                    "stability": "3/3",
                    "mutation": "KILLED",
                    "ci_parity": "yes",
                },
            },
            "ci_parity": "yes",
        },
    ],
    "flagged": [
        {
            "test_id": "test_b",
            "test_file": "tests/test_b.py",
            "verdict": {
                "verdict": "flag",
                "signals_summary": {"coverage_delta_pct": 0.0, "mutation": "SURVIVED"},
            },
        },
    ],
    "rejected": [],
    "skipped": [],
}


def test_render_test_results_carries_correlation_key_and_generated_by():
    bundle = render_test_results(
        TRIAGE, correlation_key="42", spec_id="phase3-contract-verify-001"
    )
    assert isinstance(bundle, DocBundle)
    assert bundle.correlation_key == "42"
    assert bundle.registry_entry["generated_by"] == "tfactory"
    assert bundle.registry_entry["correlation_key"] == "42"
    assert bundle.slug == "phase3-contract-verify-001-tests"
    # Markdown surfaces the moat signals + accept rate.
    assert "generated_by: tfactory" in bundle.markdown
    assert "Coverage" in bundle.markdown and "Mutation" in bundle.markdown
    assert "Accept rate" in bundle.markdown
    # 3 accepted / (3+1+1 graded) = 60%.
    assert bundle.registry_entry["accept_rate"] == 0.6


def test_emit_bundle_round_trip_resolves_by_correlation_key(tmp_path: Path):
    bundle = render_test_results(TRIAGE, correlation_key="hub-7", spec_id="spec-x")
    results = emit_bundle(bundle, targets=[RepoDocsTarget(tmp_path)])

    assert [r["status"] for r in results] == ["written"]
    # The page + registry + index were written.
    assert (tmp_path / "spec-x-tests.md").exists()
    assert (tmp_path / "registry.json").exists()

    # Resolve by the same correlation_key the plan used.
    resolver = PlanDocsResolver.from_dir(tmp_path)
    entry = resolver.resolve("hub-7")
    assert entry is not None
    assert entry["doc_file"] == "spec-x-tests.md"
    assert entry["generated_by"] == "tfactory"
    assert entry["spec_id"] == "spec-x"


def test_render_is_pure_and_deterministic():
    a = render_test_results(TRIAGE, correlation_key="k", spec_id="s")
    b = render_test_results(TRIAGE, correlation_key="k", spec_id="s")
    assert a.markdown == b.markdown
    assert a.registry_entry == b.registry_entry


def test_maybe_emit_docs_disabled_by_default(tmp_path: Path, monkeypatch):
    from agents.docs_emit_trigger import maybe_emit_docs

    monkeypatch.delenv("TFACTORY_DOCS_EMIT", raising=False)
    # Even with a valid triage report present, the emit is a no-op when off.
    findings = tmp_path / "findings"
    findings.mkdir()
    (findings / "triage_report.json").write_text(json.dumps(TRIAGE))
    assert maybe_emit_docs(tmp_path, {"spec_id": "s"}) is None


def test_maybe_emit_docs_publishes_when_enabled(tmp_path: Path, monkeypatch):
    from agents.docs_emit_trigger import maybe_emit_docs

    spec_dir = tmp_path / "spec"
    (spec_dir / "findings").mkdir(parents=True)
    (spec_dir / "context").mkdir(parents=True)
    (spec_dir / "findings" / "triage_report.json").write_text(json.dumps(TRIAGE))
    (spec_dir / "context" / "source.json").write_text(json.dumps({"issue_number": 99}))

    docs_dir = tmp_path / "docs-out"
    monkeypatch.setenv("TFACTORY_DOCS_EMIT", "1")
    monkeypatch.setenv("TFACTORY_DOCS_DIR", str(docs_dir))

    results = maybe_emit_docs(spec_dir, {"spec_id": "spec-y", "status": "triaged"})
    assert results is not None
    assert any(r["status"] == "written" for r in results)

    # The issue number (99) is the correlation key (no contract present).
    resolver = PlanDocsResolver.from_dir(docs_dir)
    assert resolver.resolve("99") is not None

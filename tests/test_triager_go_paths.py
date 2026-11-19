"""Recover package-adjacent Go tests without deduping or delivering blank sources."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from agents.triage_dedup import TriageCandidate, dedup_candidates
from agents.triager import (
    _build_candidates,
    _resolve_catalog_decisions,
    _resolve_test_source,
    _run_git_side_effect,
    run_triager,
)
from tools import git_writer
from tools.git_writer import GitWriteRequest, GitWriteResult


def _verdict(index: int, label: str = "accept") -> dict:
    return {
        "test_id": f"st{index}",
        "test_file": f"tests/case_{index}_test.go",
        "verdict": label,
        "reasons": ["evaluation reason"],
        "framework": "go",
    }


def test_spec_root_go_sources_are_recovered_without_collisions(tmp_path: Path) -> None:
    sources = [
        f"package demo\nfunc TestCase{i}(t *testing.T) {{ t.Log({i}) }}\n"
        for i in range(6)
    ]
    for i, source in enumerate(sources):
        (tmp_path / f"case_{i}_test.go").write_text(source)

    candidates = _build_candidates(tmp_path, [_verdict(i) for i in range(6)])
    assert [c.source for c in candidates] == sources
    assert [c.test_file for c in candidates] == [f"case_{i}_test.go" for i in range(6)]
    result = dedup_candidates(candidates)
    assert result.kept == tuple(candidates)
    assert result.collisions == ()


def test_blank_sources_bypass_both_dedup_passes() -> None:
    candidates = [
        TriageCandidate(f"st{i}", f"case_{i}_test.go", _verdict(i), source)
        for i, source in enumerate(("", "", " ", " ", "\n\t", "\r\n"))
    ]
    result = dedup_candidates(candidates)
    assert result.kept == tuple(candidates)
    assert result.collisions == ()


@pytest.mark.parametrize("label", ["accept", "flag"])
@pytest.mark.parametrize("contents", [None, b"", b" \n\t", b"\xff\xfe"])
def test_unreadable_sources_are_rejected_without_mutating_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, contents: bytes | None
) -> None:
    (tmp_path / "tests").mkdir()
    if contents is not None:
        (tmp_path / "tests" / "case_0_test.go").write_bytes(contents)
    verdict = _verdict(0, label)
    candidate = _build_candidates(tmp_path, [verdict])[0]
    assert not candidate.source.strip()
    assert candidate.verdict_label == "reject"
    assert candidate.verdict["reasons"] == [
        "evaluation reason",
        "test source not found or empty at tests/case_0_test.go",
    ]
    assert verdict == _verdict(0, label)
    lookup = Mock(side_effect=AssertionError("rejects must not get catalog lookups"))
    monkeypatch.setattr("agents.triager._decide_catalog_intent", lookup)
    assert _resolve_catalog_decisions([candidate], None) == {}
    lookup.assert_not_called()


def test_named_source_takes_precedence_over_spec_root_fallback(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "case_0_test.go").write_text("named source")
    (tmp_path / "case_0_test.go").write_text("fallback source")
    assert _resolve_test_source(tmp_path, "tests/case_0_test.go") == (
        "named source",
        "tests/case_0_test.go",
    )


@pytest.mark.asyncio
async def test_only_readable_source_is_delivered_in_mixed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in ("PLAN", "GENERATE", "EVALUATE", "TRIAGE"):
        monkeypatch.setenv(f"TFACTORY_AUTO_{env}", "0")
    monkeypatch.delenv("TFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("TFACTORY_TRIAGER_PR_COMMENT", raising=False)
    for sub in ("context", "findings", "tests"):
        (tmp_path / sub).mkdir()
    (tmp_path / "status.json").write_text(json.dumps({"task_id": "go-paths"}))
    (tmp_path / "context" / "source.json").write_text(
        json.dumps({"branch": "feature/go-paths"})
    )
    (tmp_path / "findings" / "verdicts.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    _verdict(i, "flag" if i == 3 else "accept") for i in range(5)
                ]
            }
        )
    )
    source = "package demo\nfunc TestCase0(t *testing.T) { t.Log(0) }\n"
    (tmp_path / "case_0_test.go").write_text(source)
    (tmp_path / "tests" / "case_2_test.go").write_text("")
    (tmp_path / "tests" / "case_3_test.go").write_text(" \n\t")
    (tmp_path / "tests" / "case_4_test.go").write_bytes(b"\xff")
    write = Mock(return_value=GitWriteResult(committed_paths=("case_0_test.go",)))
    monkeypatch.setattr(git_writer, "write_tests_to_branch", write)

    assert await run_triager(tmp_path, tmp_path) is True
    write.assert_called_once()
    request = write.call_args.args[0]
    assert isinstance(request, GitWriteRequest)
    assert request.files == (("case_0_test.go", source),)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["committed_count"] == 1
    assert status["rejected_count"] == 4
    assert status["flagged_count"] == 0
    assert status["dedup_collision_count"] == 0
    report = json.loads((tmp_path / "findings" / "triage_report.json").read_text())
    assert [c["test_id"] for c in report["committed"]] == ["st0"]
    assert [c["test_id"] for c in report["rejected"]] == ["st1", "st2", "st3", "st4"]
    markdown = (tmp_path / "findings" / "triage_report.md").read_text()
    assert "## Rejected" in markdown
    assert "test source not found or empty" in markdown


def test_git_filter_excludes_whitespace_even_without_candidate_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write = Mock(return_value=GitWriteResult())
    monkeypatch.setattr(git_writer, "write_tests_to_branch", write)
    monkeypatch.delenv("TFACTORY_TRIAGER_GIT_WRITE", raising=False)
    blank = TriageCandidate("st0", "blank_test.go", _verdict(0), " \n\t")
    result = _run_git_side_effect(tmp_path, [blank], [], {"branch": "feature/go-paths"})
    assert result == {"skipped": True, "reason": "no readable test sources"}
    write.assert_not_called()

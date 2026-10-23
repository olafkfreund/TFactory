"""A Stryker run that never happened is not_run, not no_mutant (#1419)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.confidence import compute_confidence
from agents.lang_typescript.mutate_probe import TSMutationVerdict, run_ts_mutate_probe

_BASE = {"verdict": "accept", "semantic_relevance": "low", "signals_summary": {}}


def _probe(tmp_path: Path, returncode: int, stdout: str) -> TSMutationVerdict:
    test_file = tmp_path / "calc.test.ts"
    test_file.write_text("expect(calc()).toBe(5);\n")
    report = run_ts_mutate_probe(
        test_file,
        tmp_path,
        runner_fn=lambda *_a, **_k: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=""
        ),
    )
    return report.verdict


def _report(mutants: list[dict]) -> str:
    return json.dumps({"schemaVersion": "2.0", "mutants": mutants})


@pytest.mark.parametrize("code", [126, 127])
def test_missing_stryker_is_not_run_with_no_signal_confidence(tmp_path, code):
    # Even a stray report on stdout does not count when the shell never ran Stryker.
    verdict = _probe(tmp_path, code, _report([{"id": "1", "status": "Killed"}]))
    assert verdict == TSMutationVerdict.NOT_RUN
    scored = compute_confidence(
        {**_BASE, "signals_summary": {"mutation": verdict.value}}
    )
    assert scored == compute_confidence(_BASE)


@pytest.mark.parametrize("stdout", ["", "stryker: crashed", "[1, 2]", '{"x": 1}'])
def test_no_report_is_not_run(tmp_path, stdout):
    assert _probe(tmp_path, 0, stdout) == TSMutationVerdict.NOT_RUN


def test_zero_mutant_report_is_no_mutant(tmp_path):
    assert _probe(tmp_path, 0, _report([])) == TSMutationVerdict.NO_MUTANT


def test_killed_report_unchanged(tmp_path):
    killed = _report([{"id": "1", "status": "Killed"}])
    assert _probe(tmp_path, 0, killed) == TSMutationVerdict.KILLED
    assert _probe(tmp_path, 1, killed) == TSMutationVerdict.KILLED

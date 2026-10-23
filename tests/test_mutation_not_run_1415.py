"""Exit 126/127 means the mutant never ran, across every shell-backed probe."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.confidence import compute_confidence
from agents.evaluator import _mutation_from_codes
from agents.lang_java.mutate_probe import JavaMutationVerdict, run_java_mutate_probe
from agents.lang_rust.mutate_probe import RustMutationVerdict, run_rust_mutate_probe
from agents.mutate_probe import MutationApplied, MutationVerdict, run_mutate_probe
from agents.triage_dedup import _MUTATION_PRIORITY


def _candidates(count: int) -> list[tuple[str, MutationApplied]]:
    return [
        (f"source-{n}", MutationApplied(f"op-{n}", n, "before", "after"))
        for n in range(count)
    ]


@pytest.mark.parametrize(
    ("codes", "verdict", "index"),
    [
        ([126], MutationVerdict.NOT_RUN, 0),
        ([127], MutationVerdict.NOT_RUN, 0),
        ([1], MutationVerdict.KILLED, 0),
        ([0], MutationVerdict.SURVIVED, 0),
        ([127, 127], MutationVerdict.NOT_RUN, 0),
        ([127, 126], MutationVerdict.NOT_RUN, 0),
        ([127, 0], MutationVerdict.SURVIVED, 1),
        ([126, 1], MutationVerdict.KILLED, 1),
        ([0, 1], MutationVerdict.KILLED, 1),
        ([127, 0, 0], MutationVerdict.SURVIVED, 1),
    ],
)
def test_batched_selection(codes, verdict, index):
    result = _mutation_from_codes(_candidates(len(codes)), codes)
    assert result is not None
    assert result.verdict == verdict
    assert result.mutation == _candidates(len(codes))[index][1]
    assert result.is_acceptable is (verdict == MutationVerdict.KILLED)


@pytest.mark.parametrize(
    ("codes", "verdict", "index"),
    [
        ([126], MutationVerdict.NOT_RUN, 0),
        ([127], MutationVerdict.NOT_RUN, 0),
        ([1], MutationVerdict.KILLED, 0),
        ([0], MutationVerdict.SURVIVED, 0),
        ([127, 127], MutationVerdict.NOT_RUN, 0),
        ([127, 126], MutationVerdict.NOT_RUN, 0),
        ([127, 0], MutationVerdict.SURVIVED, 1),
        ([126, 1], MutationVerdict.KILLED, 1),
        ([0, 1], MutationVerdict.KILLED, 1),
    ],
)
def test_python_probe_selection(tmp_path: Path, codes, verdict, index):
    source = "".join(
        f"def test_{n}():\n    assert {n} == {n}\n\n" for n in range(len(codes))
    )
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(source)
    seen = iter(codes)

    def runner(_test_file, _project_dir, _seed):
        return SimpleNamespace(returncode=next(seen), stdout="", stderr="")

    result = run_mutate_probe(test_file, tmp_path, runner)
    assert result.verdict == verdict
    assert result.mutation is not None
    assert result.mutation.lineno == index * 3 + 2
    assert result.is_acceptable is (verdict == MutationVerdict.KILLED)


@pytest.mark.parametrize(
    ("extension", "source", "probe", "enum"),
    [
        (
            "java",
            "assertEquals(5, calc());",
            run_java_mutate_probe,
            JavaMutationVerdict,
        ),
        ("rs", "assert_eq!(calc(), 5);", run_rust_mutate_probe, RustMutationVerdict),
    ],
)
@pytest.mark.parametrize(
    ("code", "expected"),
    [(126, "NOT_RUN"), (127, "NOT_RUN"), (1, "KILLED"), (0, "SURVIVED")],
)
def test_language_probe_return_codes(
    tmp_path: Path, extension, source, probe, enum, code, expected
):
    test_file = tmp_path / f"test.{extension}"
    test_file.write_text(source)
    report = probe(
        test_file,
        tmp_path,
        runner_fn=lambda _mutant, _project: SimpleNamespace(returncode=code),
    )
    assert report.verdict == enum[expected]


def test_not_run_drops_mutation_weight():
    base = {"verdict": "accept", "semantic_relevance": "low", "signals_summary": {}}
    no_signal = compute_confidence(base)
    result = _mutation_from_codes(_candidates(2), [127, 127])
    assert result is not None
    all_not_run = compute_confidence(
        {**base, "signals_summary": {"mutation": result.verdict.value}}
    )
    assert all_not_run == no_signal
    assert all_not_run < compute_confidence(
        {**base, "signals_summary": {"mutation": MutationVerdict.KILLED.value}}
    )
    assert _MUTATION_PRIORITY["not_run"] == _MUTATION_PRIORITY["error"]

"""Tests for the RFC-0010 equivalence lane + honest parity (Phase 6)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import equivalence_runner as eq  # noqa: E402
from agents import mutation_dispatch as md  # noqa: E402
from agents.val_block import build_verification_block  # noqa: E402

# ── value comparison (tolerance, structure) ─────────────────────────────


def test_values_match_numeric_tolerance():
    assert eq.values_match(1.0, 1.0 + 1e-12)
    assert not eq.values_match(1.0, 1.1)
    assert eq.values_match(2, 2.0)
    assert not eq.values_match(True, 1)  # bool is strict


def test_values_match_structures():
    assert eq.values_match({"a": [1, 2]}, {"a": [1, 2]})
    assert not eq.values_match([1, 2], [2, 1])  # order matters
    assert not eq.values_match({"a": 1}, {"a": 1, "b": 2})


def test_error_class_normalisation():
    assert eq.normalize_error("ValueError") == eq.normalize_error("InvalidInput")
    g = {"id": "1", "error": "ValueError"}
    c = {"id": "1", "error": "InvalidInput"}
    assert eq.vector_matches(g, c)


# ── parity report + honesty ─────────────────────────────────────────────


def _g(i, out=None, err=None, critical=False, module="m"):
    d = {"id": str(i), "module": module, "critical": critical}
    if err:
        d["error"] = err
    else:
        d["output"] = out
    return d


def test_full_parity_passes():
    golden = [_g(1, 10), _g(2, 20)]
    cand = [_g(1, 10), _g(2, 20)]
    r = eq.compare_corpus(golden, cand)
    assert r.parity_ratio == 1.0 and r.passed(1.0)
    assert "2/2" in r.claim(1.0)


def test_partial_parity_never_reads_as_full():
    golden = [_g(1, 10), _g(2, 20), _g(3, 30)]
    cand = [_g(1, 10), _g(2, 99), _g(3, 30)]  # one diverges
    r = eq.compare_corpus(golden, cand)
    assert round(r.parity_ratio, 2) == 0.67
    assert not r.passed(1.0)
    claim = r.claim(1.0)
    assert "NOT equivalent" in claim and "2/3" in claim


def test_critical_divergence_fails_even_above_threshold():
    golden = [_g(i, i) for i in range(1, 11)] + [_g(99, 1, critical=True)]
    cand = [_g(i, i) for i in range(1, 11)] + [_g(99, 2, critical=True)]
    r = eq.compare_corpus(golden, cand)
    assert r.parity_ratio > 0.9
    assert not r.passed(0.9)  # critical vector diverged
    assert "CRITICAL" in r.claim(0.9)


def test_uncovered_modules_surface_in_claim():
    golden = [_g(1, 1, module="a")]
    manifest = {"functions": [{"module": "a"}, {"module": "b"}]}
    r = eq.run_equivalence(
        manifest,
        capture_oracle=lambda m: golden,
        run_candidate=lambda m: [_g(1, 1, module="a")],
    )
    assert "b" in r.uncovered_modules
    assert "UNPROVEN" in r.claim(1.0)


# ── verdicts feed val_block at VAL-2, honestly ──────────────────────────


def test_equivalence_lane_is_val2_and_partial_fails():
    # 2 matched + 1 mismatch → reject present → VAL-2 fails → achieved capped.
    golden = [_g(1, 1), _g(2, 2), _g(3, 3)]
    cand = [_g(1, 1), _g(2, 2), _g(3, 9)]
    verdicts = eq.compare_corpus(golden, cand).verdicts()
    assert {v["lane"] for v in verdicts} == {"equivalence"}
    block = build_verification_block(verdicts)
    val2 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-2")
    assert val2["status"] == "failed"
    assert block["achieved_level"] in ("VAL-0", "VAL-1")  # capped below VAL-2


def test_full_equivalence_reaches_val2():
    verdicts = eq.compare_corpus([_g(1, 1)], [_g(1, 1)]).verdicts()
    block = build_verification_block(verdicts)
    val2 = next(lvl for lvl in block["levels"] if lvl["level"] == "VAL-2")
    assert val2["status"] == "passed"


# ── rust mutation backend ───────────────────────────────────────────────


def test_rust_mutation_supported():
    assert md.is_mutation_supported("rust")
    assert md.is_mutation_supported("rs")
    assert md.mutant_extension("rust") == "rs"


def test_rust_mutate_source_bumps_assert_eq():
    from agents.lang_rust.mutate_probe import mutate_source

    mutated, desc = mutate_source("    assert_eq!(refund(100), 100);\n")
    assert "101" in mutated and "100 → 101" in desc


def test_rust_probe_killed_when_runner_fails(tmp_path: Path):
    from agents.lang_rust.mutate_probe import RustMutationVerdict, run_rust_mutate_probe

    tf = tmp_path / "t.rs"
    tf.write_text("#[test]\nfn t() { assert_eq!(add(2, 2), 4); }\n")

    class _R:
        returncode = 1  # mutant failed → test is meaningful → KILLED

    report = run_rust_mutate_probe(tf, tmp_path, runner_fn=lambda *a: _R())
    assert report.verdict == RustMutationVerdict.KILLED

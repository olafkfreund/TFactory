"""Tests for live equivalence-lane execution + cargo-mutants (RFC-0010 gap)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import equivalence_lane as el  # noqa: E402
from agents.lang_rust import cargo_mutants as cm  # noqa: E402


@dataclass
class _Result:
    stdout: str
    returncode: int = 0


def _runner(stdout):
    return lambda *a, **k: _Result(stdout=stdout)


_MANIFEST = {
    "functions": [{"module": "pay/refund.py", "name": "refund"}],
    "input_vectors": [
        {
            "id": "1",
            "module": "pay/refund.py",
            "function": "refund",
            "args": [100],
            "critical": True,
        },
        {"id": "2", "module": "pay/refund.py", "function": "refund", "args": [-1]},
    ],
}


# ── harness + parsing ───────────────────────────────────────────────────


def test_oracle_harness_is_protocol_shaped():
    src = el.generate_python_oracle_harness()
    assert "json.load(sys.stdin)" in src and "type(exc).__name__" in src


def test_input_vectors_explicit_and_fallback():
    assert len(el.input_vectors(_MANIFEST)) == 2
    fb = el.input_vectors({"functions": [{"module": "m.py", "name": "f"}]})
    assert len(fb) == 1 and fb[0]["function"] == "f"


def test_parse_results_tolerates_log_noise():
    out = 'some log line\n[{"id": "1", "output": 100}]\n'
    assert el._parse_results(out) == [{"id": "1", "output": 100}]
    assert el._parse_results("") == []


def test_parse_results_python_literal_fallback():
    # Some k8s pod-log clients re-serialise stdout as a Python literal (single
    # quotes); the data is the same and must still parse.
    out = "[{'id': '1', 'output': {'refunded': 100}}]"
    assert el._parse_results(out) == [{"id": "1", "output": {"refunded": 100}}]


# ── capture + candidate via injected runners ────────────────────────────


def test_capture_oracle_writes_harness_and_parses(tmp_path: Path):
    canned = json.dumps([{"id": "1", "module": "pay/refund.py", "output": 100}])
    golden = el.capture_oracle(tmp_path, _MANIFEST, _runner(canned))
    assert golden[0]["output"] == 100
    assert (tmp_path / ".tfactory_oracle_harness.py").is_file()


# ── full lane: parity pass / fail, honest, writes findings ──────────────


def test_run_equivalence_lane_full_parity(tmp_path: Path):
    same = json.dumps(
        [
            {"id": "1", "module": "pay/refund.py", "output": 100},
            {"id": "2", "module": "pay/refund.py", "error": "ValueError"},
        ]
    )
    contract = {
        "tfactory": {"equivalence": {"parity_threshold": 1.0, "manifest": _MANIFEST}}
    }
    res = el.run_equivalence_lane(
        contract,
        oracle_root=tmp_path / "o",
        candidate_root=tmp_path / "c",
        oracle_runner=_runner(same),
        candidate_runner=_runner(same),
        findings_dir=tmp_path / "findings",
    )
    assert res["passed"] and res["parity_ratio"] == 1.0
    assert all(v["verdict"] == "accept" for v in res["verdicts"])
    assert (tmp_path / "findings" / "golden_corpus.json").is_file()


def test_run_equivalence_lane_critical_divergence_fails(tmp_path: Path):
    (tmp_path / "o").mkdir()
    (tmp_path / "c").mkdir()
    oracle = json.dumps(
        [
            {"id": "1", "module": "pay/refund.py", "output": 100},
            {"id": "2", "module": "pay/refund.py", "error": "ValueError"},
        ]
    )
    candidate = json.dumps(
        [
            {
                "id": "1",
                "module": "pay/refund.py",
                "output": 999,
            },  # critical vector diverges
            {"id": "2", "module": "pay/refund.py", "error": "ValueError"},
        ]
    )
    contract = {
        "tfactory": {"equivalence": {"parity_threshold": 0.5, "manifest": _MANIFEST}}
    }
    res = el.run_equivalence_lane(
        contract,
        oracle_root=tmp_path / "o",
        candidate_root=tmp_path / "c",
        oracle_runner=_runner(oracle),
        candidate_runner=_runner(candidate),
    )
    assert res["passed"] is False  # critical vector diverged, even above threshold
    assert "CRITICAL" in res["claim"]
    assert any(v["verdict"] == "reject" for v in res["verdicts"])


# ── cargo-mutants ───────────────────────────────────────────────────────


def test_parse_cargo_mutants_strong_weak_none():
    strong = cm.parse_cargo_mutants_output("30 mutants tested: 30 caught, 0 missed")
    assert strong.verdict == cm.CargoMutantsVerdict.STRONG and strong.score == 1.0
    weak = cm.parse_cargo_mutants_output("30 mutants tested: 27 caught, 3 missed")
    assert weak.verdict == cm.CargoMutantsVerdict.WEAK and weak.missed == 3
    none = cm.parse_cargo_mutants_output("0 mutants tested")
    assert none.verdict == cm.CargoMutantsVerdict.NONE


def test_parse_cargo_mutants_error_on_garbage():
    assert cm.parse_cargo_mutants_output("boom").verdict == cm.CargoMutantsVerdict.ERROR


def test_run_cargo_mutants_via_runner(tmp_path: Path):
    report = cm.run_cargo_mutants(
        tmp_path, runner_fn=_runner("12 mutants tested: 12 caught, 0 missed")
    )
    assert report.verdict == cm.CargoMutantsVerdict.STRONG


def test_run_cargo_mutants_no_runner_is_error(tmp_path: Path):
    assert cm.run_cargo_mutants(tmp_path).verdict == cm.CargoMutantsVerdict.ERROR


# ── run_from_spec: orchestrate + merge verdicts (injected runners) ──────


def test_run_from_spec_merges_verdicts(tmp_path: Path):
    spec = tmp_path / "spec"
    project = tmp_path / "proj"
    project.mkdir()
    # a pre-existing unit verdict the equivalence verdicts must be appended to
    (spec / "findings").mkdir(parents=True)
    (spec / "findings" / "verdicts.json").write_text(
        json.dumps({"verdicts": [{"lane": "unit", "verdict": "accept"}]})
    )
    same = json.dumps(
        [
            {"id": "1", "module": "pay/refund.py", "output": 100},
            {"id": "2", "module": "pay/refund.py", "error": "ValueError"},
        ]
    )
    contract = {
        "tfactory": {"equivalence": {"parity_threshold": 1.0, "manifest": _MANIFEST}}
    }
    res = el.run_from_spec(
        spec,
        project,
        contract,
        oracle_runner=_runner(same),
        candidate_runner=_runner(same),
    )
    assert res["passed"]
    merged = json.loads((spec / "findings" / "verdicts.json").read_text())["verdicts"]
    lanes = [v["lane"] for v in merged]
    assert "unit" in lanes and lanes.count("equivalence") == 2  # appended, not replaced


def test_run_from_spec_noop_without_equivalence(tmp_path: Path):
    assert el.run_from_spec(tmp_path, tmp_path, {"tfactory": {}}) is None


def test_harness_reads_vectors_from_file_arg(tmp_path: Path):
    # The generated harness must accept argv[1] (sandbox file) — exercise it for
    # real against a tiny module so the protocol is proven end to end.
    import subprocess

    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "h.py").write_text(el.generate_python_oracle_harness())
    (tmp_path / "v.json").write_text(
        json.dumps([{"id": "1", "module": "m.py", "function": "add", "args": [2, 3]}])
    )
    out = subprocess.run(
        [sys.executable, "h.py", "v.json"], cwd=tmp_path, capture_output=True, text=True
    )
    assert el._parse_results(out.stdout)[0]["output"] == 5

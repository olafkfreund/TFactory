"""Tests for live equivalence-lane execution + cargo-mutants (RFC-0010 gap)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import equivalence_lane as el  # noqa: E402
from agents.lang_rust import cargo_mutants as cm  # noqa: E402


@dataclass
class _Result:
    stdout: str
    returncode: int = 0
    stderr: str = ""


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


# ── a dead harness is not an empty corpus (TFactory#959) ────────────────


def _dead_runner(rc: int = 2):
    """A harness that never ran: nothing on stdout, non-zero exit."""
    return lambda *a, **k: _Result(
        stdout="", returncode=rc, stderr="python: can't open file 'harness.py'"
    )


def test_dead_harness_is_not_a_golden_corpus(tmp_path: Path):
    with pytest.raises(el.HarnessDidNotRunError, match="oracle harness exited 2"):
        el.capture_oracle(tmp_path, _MANIFEST, _dead_runner())
    with pytest.raises(el.HarnessDidNotRunError, match="candidate harness exited 2"):
        el.run_candidate(tmp_path, _MANIFEST, _dead_runner())


def test_runner_without_a_status_fails_closed(tmp_path: Path):
    """A runner that cannot report an exit code cannot show its harness ran."""
    statusless = lambda *a, **k: type("R", (), {"stdout": ""})()  # noqa: E731
    with pytest.raises(el.HarnessDidNotRunError, match="no exit status"):
        el.capture_oracle(tmp_path, _MANIFEST, statusless)


def test_harness_that_emitted_results_but_exited_nonzero_is_kept(tmp_path: Path):
    """Results on stdout prove it ran, whatever status a wrapper propagated."""
    canned = json.dumps([{"id": "1", "module": "pay/refund.py", "output": 100}])
    golden = el.capture_oracle(
        tmp_path, _MANIFEST, lambda *a, **k: _Result(stdout=canned, returncode=3)
    )
    assert golden[0]["output"] == 100


def test_lane_names_the_dead_harness_not_the_manifest(tmp_path: Path):
    contract = {"tfactory": {"equivalence": {"manifest": _MANIFEST}}}
    res = el.run_equivalence_lane(
        contract,
        oracle_root=tmp_path / "o",
        candidate_root=tmp_path / "c",
        oracle_runner=_dead_runner(),
        candidate_runner=_dead_runner(),
        findings_dir=tmp_path / "findings",
    )
    assert res["passed"] is False
    assert len(res["verdicts"]) == 1
    verdict = res["verdicts"][0]
    assert verdict["lane"] == "equivalence" and verdict["verdict"] == "reject"
    assert "did not run" in verdict["reason"]
    assert "NOT MEASURED" in res["claim"] and "oracle harness" in res["claim"]
    # The old verdict blamed the manifest for a failure that was not there.
    assert "UNPROVEN" not in res["claim"] and "corpus coverage" not in res["claim"]
    # No stable, meaningless digest of [].
    assert not (tmp_path / "findings" / "golden_corpus.json").exists()
    report = json.loads((tmp_path / "findings" / "equivalence_report.json").read_text())
    assert report["measured"] is False


def test_lane_rejects_a_dead_harness_even_when_nothing_is_declared(tmp_path: Path):
    """The path with no second line of defence: `declared - covered` is empty."""
    contract = {"tfactory": {"equivalence": {"manifest": {"input_vectors": []}}}}
    res = el.run_equivalence_lane(
        contract,
        oracle_root=tmp_path / "o",
        candidate_root=tmp_path / "c",
        oracle_runner=_dead_runner(),
        candidate_runner=_dead_runner(),
    )
    assert res["passed"] is False
    assert [v["verdict"] for v in res["verdicts"]] == ["reject"]
    assert "NOT MEASURED" in res["claim"]


def test_empty_corpus_from_a_live_harness_is_a_measurement(tmp_path: Path):
    """Exit 0 with `[]` RAN. It must not raise, and must not pass either."""
    alive = lambda *a, **k: _Result(stdout="[]\n", returncode=0)  # noqa: E731
    assert el.capture_oracle(tmp_path, _MANIFEST, alive) == []
    res = el.run_equivalence_lane(
        {"tfactory": {"equivalence": {"manifest": {"input_vectors": []}}}},
        oracle_root=tmp_path / "o",
        candidate_root=tmp_path / "c",
        oracle_runner=alive,
        candidate_runner=alive,
        findings_dir=tmp_path / "findings",
    )
    assert res["passed"] is False  # 0/0 is not parity
    assert "NOT MEASURED" not in res["claim"]
    assert "0/0 golden vectors" in res["claim"]
    # It measured, so the (empty) corpus is a real artefact and gets written.
    assert (tmp_path / "findings" / "golden_corpus.json").is_file()


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

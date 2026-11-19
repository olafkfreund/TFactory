"""Evaluator wiring for the Rust cargo-test lane (#1578).

Before this lane a Rust subtask matched no filter and was silently dropped, so a
Rust-only plan fell through to ``evaluated_empty``.
"""

from __future__ import annotations

from pathlib import Path

from agents.evaluator import (
    _build_all_bundles,
    _completed_functional_subtasks,
    _completed_go_subtasks,
    _completed_kotlin_subtasks,
    _completed_rust_subtasks,
    _resolve_rust_runner_fn,
)
from agents.stability_runner import StabilityVerdict
from tools.runners.docker_runner import DockerRunResult

_OK = (
    "     Running tests/calc.rs (target/debug/deps/calc-1)\n"
    "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
)


def _plan(*subtasks: dict) -> dict:
    return {"phases": [{"phase": 1, "name": "main", "subtasks": list(subtasks)}]}


def _rust_subtask(stid: str = "st-rs-0", rel: str = "tests/calc.rs") -> dict:
    return {
        "id": stid,
        "status": "completed",
        "lane": "unit",
        "language": "rust",
        "target": "calc::add",
        "rationale": "AC#1",
        "files_to_create": [rel],
    }


def _stage(spec_dir: Path, *rels: str) -> None:
    for rel in rels:
        (spec_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / rel).write_text("#[test]\nfn t() {}\n")


def test_rust_subtask_reaches_only_the_rust_lane() -> None:
    plan = _plan(
        _rust_subtask("st-rs"),
        {**_rust_subtask("st-rs-pending"), "status": "pending"},
    )
    assert [s["id"] for s in _completed_rust_subtasks(plan)] == ["st-rs"]
    assert _completed_functional_subtasks(plan) == []
    assert _completed_go_subtasks(plan) == []
    assert _completed_kotlin_subtasks(plan) == []


def test_rust_runner_fails_closed_when_sandbox_unconfigured(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agents.nix_rust_lane.run_cargo_lane_via_nix", lambda *a, **k: None
    )
    runner = _resolve_rust_runner_fn(tmp_path / "spec", tmp_path / "proj")
    res = runner(tmp_path / "spec" / "tests" / "calc.rs", tmp_path / "proj", 0)
    assert res.returncode == 1
    assert "rust nix lane unavailable" in res.stderr


def test_build_all_bundles_runs_rust_through_the_cargo_lane(
    tmp_path: Path, monkeypatch
) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    _stage(spec_dir, "tests/calc.rs")
    calls: list[Path | None] = []

    def _fake(spec, proj, *, hint=None, **k):
        calls.append(hint)
        return DockerRunResult(returncode=0, stdout=_OK, argv=["cargo"])

    monkeypatch.setattr("agents.nix_rust_lane.run_cargo_lane_via_nix", _fake)
    rust = _completed_rust_subtasks(_plan(_rust_subtask()))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], rust=rust)
    assert (project_dir / "tests" / "calc.rs").is_file()  # staged for cargo
    assert len(bundles) == 1 and bundles[0].test_id == "st-rs-0"
    assert bundles[0].stability.verdict == StabilityVerdict.STABLE
    assert bundles[0].mutation is None
    assert calls and calls[0] == Path("tests/calc.rs")


def test_one_stability_pass_per_crate(tmp_path: Path, monkeypatch) -> None:
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    subs = [_rust_subtask(f"st-{i}", f"tests/t{i}.rs") for i in range(3)]
    _stage(spec_dir, *[s["files_to_create"][0] for s in subs])
    runs: list[int] = []
    out = (
        "".join(
            f"     Running tests/t{i}.rs (target/debug/deps/t{i}-1)\n" for i in range(3)
        )
        + "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured\n"
    )

    def _fake(spec, proj, *, hint=None, **k):
        runs.append(1)
        return DockerRunResult(returncode=0, stdout=out, argv=["cargo"])

    monkeypatch.setattr("agents.nix_rust_lane.run_cargo_lane_via_nix", _fake)
    rust = _completed_rust_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], rust=rust)
    assert len(bundles) == 3
    assert len(runs) == 3  # one crate-wide 3x pass, not 3 per subtask


def test_a_target_cargo_never_ran_is_error_while_its_sibling_is_graded(
    tmp_path: Path, monkeypatch
) -> None:
    """Aggregate passes from other tests must not vouch for an unrun file."""
    spec_dir, project_dir = tmp_path / "spec", tmp_path / "project"
    subs = [
        _rust_subtask("st-ran", "tests/calc.rs"),
        _rust_subtask("st-lost", "tests/lost.rs"),
    ]
    _stage(spec_dir, "tests/calc.rs", "tests/lost.rs")

    def _fake(spec, proj, *, hint=None, **k):
        return DockerRunResult(returncode=0, stdout=_OK, argv=["cargo"])

    monkeypatch.setattr("agents.nix_rust_lane.run_cargo_lane_via_nix", _fake)
    rust = _completed_rust_subtasks(_plan(*subs))
    bundles = _build_all_bundles(spec_dir, project_dir, [], [], [], [], [], rust=rust)
    got = {b.test_id: b.stability.verdict for b in bundles}
    assert got == {
        "st-ran": StabilityVerdict.STABLE,
        "st-lost": StabilityVerdict.ERROR,
    }

"""TFactory cannot gain a gate or a verdict rule without a proof that it refuses.

TFactory#1247. Every evidence gate here already has tests. What was missing is
the property one level up: nothing failed when a *new* gate arrived with no
proof that it goes red. A suite that only ever grows by hand narrows silently —
the gates keep passing, the coverage claim keeps reading the same, and the one
direction that costs a missing control (a gate that never refuses anything)
looks identical to a gate nobody broke.

The hub solved this in ``Factory/tests/test_gate_honesty.py``: a registry
enumerated against the directory, so a ``scripts/check_*.py`` with no case fails
rather than widening the blind spot. This is that shape, ported to TFactory's
surface — not its code.

**What the registry enumerates, and why.** TFactory has no single naming
convention that covers its refusal surface, so the registry is the union of two
mechanical scans, neither of which a new gate can avoid tripping:

1. ``_gate_modules()`` — ``*_gate.py`` under ``apps/backend/agents/`` and
   ``scripts/``. TFactory's own name for a module whose job is to return a
   pass/fail verdict.
2. ``_verdict_rules()`` — every named rule in ``agents/triager.py`` that can
   move a completion envelope's ``outcome`` off ``success``, read out of the
   ``halt_reason`` string literals by AST. A gate that refuses by holding a
   completion back must write one, whatever its own module is called.

The second scan is why the first being narrow is not the hole it looks like:
``dependency_review.py`` and ``criterion_conflict.py`` are gates whose filenames
scan 1 misses entirely, and scan 2 catches both — through the only mechanism by
which they can actually block anything. A registry that enumerated a hand-picked
list of "modules that feel like gates" would be worse than none, because the
list can be short and still look complete.

Each member is asserted below by the case named in ``_COVERED``, or named in
``_EXEMPT`` with its reason. An exemption nobody can see is the same green as a
check nobody wrote.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _ROOT / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import coverage_gate  # noqa: E402
from agents import health_gate, quality_gate, verification_gate  # noqa: E402
from agents.triager import _build_completion_envelope  # noqa: E402

_TRIAGER = _BACKEND_DIR / "agents" / "triager.py"

# Registry member -> the case in THIS file that proves it refuses.
_COVERED: dict[str, str] = {
    # scan 1: *_gate.py
    "health_gate.py": "test_health_gate_refuses_a_failing_check",
    "quality_gate.py": "test_quality_gate_refuses_a_contradictory_accept",
    "verification_gate.py": "test_verification_gate_refuses_an_overclaim",
    "coverage_gate.py": "test_coverage_gate_refuses_a_drop_and_an_empty_report",
    # scan 2: triager halt_reason rules
    "no_evidence": "test_no_evidence_rule_refuses_a_verdict_free_run",
    "dependency_review": "test_dependency_review_rule_refuses_a_gating_fail",
    "criterion_conflict": "test_criterion_conflict_rule_refuses_a_gating_conflict",
    "zero_tests": "test_zero_tests_rule_refuses_an_unexplained_empty_generation",
    "delivery_failed": "test_delivery_failed_rule_refuses_an_uncommitted_accept",
}

# Members deliberately out of scope, each with the reason stated.
_EXEMPT: dict[str, str] = {}


def _gate_modules() -> set[str]:
    """Scan 1: modules TFactory itself names as gates."""
    return {
        path.name
        for path in [
            *(_BACKEND_DIR / "agents").glob("*_gate.py"),
            *(_ROOT / "scripts").glob("*_gate.py"),
        ]
    }


def _rule_name(value: ast.expr) -> str | None:
    """The ``<rule>:`` prefix of a halt_reason literal, or None if it has none.

    Handles both shapes the triager writes: a bare constant, and a
    ``"<rule>: " + str(...)`` concatenation. A ``halt_reason = halt_reason``
    re-assignment carries no literal and is plumbing, not a rule.
    """
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
        value = value.left
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    head, sep, _ = value.value.partition(":")
    return head.strip() if sep and head.strip() else None


def _verdict_rules() -> set[str]:
    """Scan 2: named rules in the triager that can hold a completion back."""
    tree = ast.parse(_TRIAGER.read_text())
    rules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            named = (isinstance(target, ast.Name) and target.id == "halt_reason") or (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "halt_reason"
            )
            if named:
                rule = _rule_name(node.value)
                if rule is not None:
                    rules.add(rule)
    return rules


def test_every_gate_carries_a_refusal_proof() -> None:
    """The registry is enumerated against the code, not counted against itself.

    A ``*_gate.py`` or a triager halt_reason rule that nobody wrote a refusal
    case for fails here, which is the whole point: the blind spot widens by
    exactly the amount nobody notices otherwise.
    """
    found = _gate_modules() | _verdict_rules()
    assert found, (
        "both registry scans came back empty — the scan is broken, not the repo"
    )
    assert found == set(_COVERED) | set(_EXEMPT), (
        "a gate or verdict rule is neither proven to refuse here nor named as "
        f"exempt: {sorted(found - set(_COVERED) - set(_EXEMPT))}; "
        f"registered but no longer present: {sorted((set(_COVERED) | set(_EXEMPT)) - found)}"
    )
    defined = {
        node.name
        for node in ast.parse(Path(__file__).read_text()).body
        if isinstance(node, ast.FunctionDef)
    }
    missing = {m: c for m, c in _COVERED.items() if c not in defined}
    assert not missing, f"registered cases that do not exist: {missing}"


# --------------------------------------------------------------------------- #
# scan 1 — the gate modules
# --------------------------------------------------------------------------- #


def test_health_gate_refuses_a_failing_check() -> None:
    cfg = {"path": "/healthz", "expect_status": 200}
    unhealthy = health_gate.gate("http://127.0.0.1:9", cfg, opener=lambda _u, _t: 503)
    assert unhealthy.ok is False, "a configured health check returning 503 must refuse"
    assert "503" in unhealthy.detail
    healthy = health_gate.gate("http://127.0.0.1:9", cfg, opener=lambda _u, _t: 200)
    assert healthy.ok is True

    # The gate's other refusal, and the one that must never fail open: an
    # untrusted target URL out of the tested repo's own .tfactory.yml.
    blocked = health_gate.gate("http://169.254.169.254", cfg, opener=lambda _u, _t: 200)
    assert blocked.ok is False, "cloud metadata must be refused before any fetch"
    assert blocked.detail.startswith("blocked_unsafe_target:")


def _write_verdicts(path: Path, verdicts: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"verdicts": verdicts}))
    return path


def test_quality_gate_refuses_a_contradictory_accept(tmp_path: Path) -> None:
    accepted = {
        "test_id": "t1",
        "verdict": "accept",
        "signals_summary": {"stability": "stable", "mutation": "killed"},
    }
    clean = _write_verdicts(tmp_path / "clean.json", [accepted])
    assert quality_gate.evaluate_gate(clean, quality_gate.GatePolicy()).passed is True

    survived = dict(
        accepted, signals_summary={"stability": "stable", "mutation": "survived"}
    )
    bad = _write_verdicts(tmp_path / "bad.json", [survived])
    result = quality_gate.evaluate_gate(bad, quality_gate.GatePolicy())
    assert result.passed is False, "an accept whose mutant survived must refuse"
    assert result.state == quality_gate.STATE_FAILURE
    assert any("survived" in reason for reason in result.reasons)

    empty = _write_verdicts(tmp_path / "empty.json", [])
    assert (
        quality_gate.evaluate_gate(empty, quality_gate.GatePolicy()).passed is False
    ), "zero evaluated tests is nothing proven, not a pass"


def test_verification_gate_refuses_an_overclaim() -> None:
    block = verification_gate.normalize_verification(
        {
            "target_level": "VAL-3",
            "achieved_level": "VAL-3",
            "levels": [
                {"level": "VAL-1", "status": "passed"},
                {
                    "level": "VAL-2",
                    "status": "failed",
                    "reason": "integration lane red",
                },
                {"level": "VAL-3", "status": "passed"},
            ],
        }
    )
    assert block["achieved_level"] == "VAL-1", "a failed floor must cap the ceiling"
    assert block["_gate"]["downgraded"] is True
    assert any(v.startswith("overclaim:") for v in block["_gate"]["violations"])

    empty = verification_gate.normalize_verification(None)
    assert empty["achieved_level"] == "VAL-0", "no block declared is never 'tested'"
    assert "missing_verification_block" in empty["_gate"]["violations"]


def _cobertura(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_coverage_gate_refuses_a_drop_and_an_empty_report(tmp_path: Path) -> None:
    base = coverage_gate.Measurement(80.0, 80, 100)
    dropped = coverage_gate.Measurement(70.0, 70, 100)
    passed, description = coverage_gate.verdict(dropped, base, tolerance=0.5)
    assert passed is False, "a coverage drop beyond tolerance must refuse"
    assert "dropped" in description
    assert coverage_gate.verdict(base, base, tolerance=0.5)[0] is True

    # A report with no counts must raise, not read as 0.0% (a fake collapse) or
    # as a pass — the gate has no measurement, which is not a verdict.
    blank = _cobertura(tmp_path / "blank.xml", '<?xml version="1.0"?><coverage/>')
    try:
        coverage_gate.read_measurement(blank)
    except coverage_gate.GateError:
        pass
    else:  # pragma: no cover - only reached when the gate stops refusing
        raise AssertionError(
            "a countless coverage report must raise, not return a number"
        )


# --------------------------------------------------------------------------- #
# scan 2 — the triager's completion-verdict rules
# --------------------------------------------------------------------------- #


def _status(**overrides: Any) -> dict[str, Any]:
    base = {
        "task_id": "1247",
        "status": "triaged",
        "verdicts_count": 3,
        "committed_count": 2,
        "flagged_count": 1,
    }
    base.update(overrides)
    return base


def _finding(spec_dir: Path, name: str, block: dict[str, Any]) -> None:
    findings = spec_dir / "findings"
    findings.mkdir(parents=True, exist_ok=True)
    (findings / name).write_text(json.dumps(block))


def test_no_evidence_rule_refuses_a_verdict_free_run(tmp_path: Path) -> None:
    envelope = _build_completion_envelope(
        tmp_path, _status(verdicts_count=0, committed_count=0, flagged_count=0)
    )
    assert envelope["outcome"] == "failure", (
        "a 'triaged' run that evaluated nothing must not report success"
    )
    assert envelope["halt_reason"].startswith("no_evidence:")
    assert _build_completion_envelope(tmp_path, _status())["outcome"] == "success"


def test_dependency_review_rule_refuses_a_gating_fail(tmp_path: Path) -> None:
    _finding(
        tmp_path,
        "dependency_review.json",
        {
            "status": "fail",
            "gating": True,
            "reason": "unpinned dependency 'httpx' added",
        },
    )
    envelope = _build_completion_envelope(tmp_path, _status())
    assert envelope["outcome"] == "human_review", (
        "a failing dependency review must downgrade a would-be success"
    )
    assert envelope["halt_reason"].startswith("dependency_review:")


def test_zero_tests_rule_refuses_an_unexplained_empty_generation(
    tmp_path: Path,
) -> None:
    """#1253 — and it must refuse in ONE direction only.

    Both directions are asserted here on purpose: a rule that failed every zero
    would be a false-refusal generator, which is the same defect wearing the
    opposite sign.
    """
    unexplained = _build_completion_envelope(tmp_path, _status(tests_generated=0))
    assert unexplained["outcome"] == "failure", (
        "a verify that generated 0 tests and stated no reason must not report "
        "success — nothing was tested"
    )
    assert unexplained["halt_reason"].startswith("zero_tests:")

    # A stated skip is legitimate and must stay distinguishable: not a success,
    # not a refusal, and carrying the reason.
    skipped = _build_completion_envelope(
        tmp_path,
        _status(tests_generated=0, verify_skip_reason="no pending subtasks"),
    )
    assert skipped["outcome"] == "empty", (
        "an explicitly-skipped generation must not be reported as a failure"
    )
    assert skipped["halt_reason"] != unexplained["halt_reason"], (
        "a stated skip and a silent empty must not render identically"
    )
    assert "no pending subtasks" in skipped["halt_reason"]

    # A run that never recorded the count is not the same measurement as zero.
    assert _build_completion_envelope(tmp_path, _status())["outcome"] == "success"


def test_delivery_failed_rule_refuses_an_uncommitted_accept(tmp_path: Path) -> None:
    """#1260 — accepted tests that reached no branch are not a delivered verify.

    Both directions on purpose: a rule that refused every write would be a
    false-refusal generator, and one that refused none is the defect itself.
    """
    failed = _build_completion_envelope(
        tmp_path,
        _status(
            accepted_count=5,
            committed_count=0,
            git_writer={"ok": False, "committed_paths": [], "error": "checkout failed"},
        ),
    )
    assert failed["outcome"] == "failure", (
        "a run whose git write failed delivered nothing and must not report a "
        "successful verification"
    )
    assert failed["halt_reason"].startswith("delivery_failed:")
    assert "5" in failed["halt_reason"], "the accepted-but-undelivered count"

    # The inverse: a successful write is untouched, and still reports what landed.
    ok = _build_completion_envelope(
        tmp_path,
        _status(
            accepted_count=5,
            committed_count=5,
            git_writer={"ok": True, "committed_paths": ["tests/test_a.py"]},
        ),
    )
    assert ok["outcome"] == "success", "a delivered verify must still pass"
    assert ok["result"]["committed_count"] == 5


def test_criterion_conflict_rule_refuses_a_gating_conflict(tmp_path: Path) -> None:
    _finding(
        tmp_path,
        "criterion_conflict.json",
        {"gating": True, "reason": "AC-1 and AC-3 are mutually unsatisfiable"},
    )
    envelope = _build_completion_envelope(tmp_path, _status())
    assert envelope["outcome"] == "human_review", (
        "mutually unsatisfiable acceptance criteria must route to a human"
    )
    assert envelope["halt_reason"].startswith("criterion_conflict:")

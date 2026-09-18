"""Which verdict reasons a model wrote, and which the code measured (#1195).

The handback printed every reason as "Observed:" and AIFactory's QA Fixer acts
on it as instructions — but ``reasons`` is the judge LLM's prose *plus* lines
five deterministic writers add or substitute. Provenance is recorded where each
line is written, in ``reasons_source``, so a renderer can label each one.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from agents.confidence import (
    add_system_reason,
    apply_app_not_healthy_override,
    apply_consistent_fail_reason,
    apply_flaky_override,
    reason_lines,
    set_system_reasons,
)

AGENTS = Path(__file__).resolve().parents[1] / "apps" / "backend" / "agents"


def _judged(*reasons: str, **extra: object) -> dict:
    return {"test_id": "t1", "verdict": "accept", "reasons": list(reasons), **extra}


# ── helpers ──────────────────────────────────────────────────────────────


def test_add_system_reason_backfills_model_then_tags_its_own_line():
    v = _judged("judge says the subject is fine")
    add_system_reason(v, "measured fact")
    assert v["reasons"] == ["judge says the subject is fine", "measured fact"]
    assert v["reasons_source"] == ["model", "system"]


def test_add_system_reason_never_mutates_a_shared_list():
    """The vote merge shallow-copies a judge's entry; appending in place would
    rewrite that judge's own record (and its dissent copy) too."""
    judge_entry = _judged("judge: accept")
    merged = dict(judge_entry)
    add_system_reason(merged, "majority vote overrides")
    assert judge_entry["reasons"] == ["judge: accept"]


def test_set_system_reasons_replaces_both_lists():
    v = _judged("a wrong guess", "another")
    set_system_reasons(v, ["the classifier's answer"])
    assert v["reasons"] == ["the classifier's answer"]
    assert v["reasons_source"] == ["system"]


def test_a_legacy_verdict_reads_as_model():
    """No reasons_source (older runs): every line is the model's — the safe
    direction, since an opinion shown as fact is the defect being fixed."""
    assert reason_lines(_judged("x", "y")) == [("x", "model"), ("y", "model")]


def test_a_short_source_list_defaults_the_rest_to_model():
    v = _judged("x", "y")
    v["reasons_source"] = ["system"]
    assert reason_lines(v) == [("x", "system"), ("y", "model")]


# ── the five deterministic writers ───────────────────────────────────────


def test_flaky_demotion_tags_its_line_system():
    v = _judged(
        "judge: looks good",
        signals_summary={"flaky": {"classification": "flaky", "flip_rate": 0.4}},
    )
    assert apply_flaky_override(v)
    assert reason_lines(v)[0] == ("judge: looks good", "model")
    assert reason_lines(v)[1][1] == "system"


def test_consistent_fail_classifier_replaces_with_system_only():
    v = _judged(
        "judge: module not importable",
        signals_summary={"stability": "consistent_fail"},
    )
    assert apply_consistent_fail_reason(
        v, {"failure_kind": "assertion", "rerun_count": 3}
    )
    assert v["reasons_source"] == ["system"]
    assert len(v["reasons"]) == 1


def test_app_not_healthy_tags_its_line_system():
    v = _judged("judge: endpoint wrong")
    v["verdict"] = "reject"
    assert apply_app_not_healthy_override(v, {"failure_kind": "app_not_healthy"})
    assert [s for _, s in reason_lines(v)] == ["model", "system"]


def test_majority_vote_override_tags_its_line_system():
    from agents.evaluator import _merge_voted_verdicts

    doc = {"verdicts": [_judged("judge: accept it")]}
    merged, _summary = asyncio.run(_merge_voted_verdicts([doc, None, None]))
    entry = merged["verdicts"][0]
    assert entry["verdict"] == "reject"
    assert [s for _, s in reason_lines(entry)] == ["model", "system"]


def test_unjudged_entry_is_all_system():
    from agents.evaluator import _unjudged_entry

    entry = _unjudged_entry("t9", "tests/t9.test.js")
    assert entry["reasons_source"] == ["system"] * len(entry["reasons"])


# ── enforcement: no writer may bypass the helpers ────────────────────────

# (path under agents/, function) pairs allowed to touch a "reasons" key
# directly: the helpers themselves, the quality gate's own (non-verdict) output
# dict, and the hand-back payload that serialises the provenance.
_ALLOWED = {
    ("confidence.py", "add_system_reason"),
    ("confidence.py", "set_system_reasons"),
    ("triager.py", "_run_pr_status_side_effect"),
    ("handback/request.py", "to_dict"),
}


def _is_reasons_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "reasons"


def _dissent_records(tree: ast.AST) -> set[int]:
    """ids of dict literals under a ``"dissent"`` key.

    The #649 vote block keeps a verbatim copy of each dissenting judge's own
    reasons for calibration. That is not the verdict's ``reasons`` and no
    renderer shows it as such, so it is not a provenance write.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values, strict=False):
            if isinstance(k, ast.Constant) and k.value == "dissent":
                out |= {id(d) for d in ast.walk(v) if isinstance(d, ast.Dict)}
    return out


def _reasons_writes(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of every direct write to a reasons list."""
    hits: list[tuple[str, int]] = []
    dissent = _dissent_records(tree)

    def visit(fn: str, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else fn
            )
            visit(name, child)
        if id(node) in dissent:
            return
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and _is_reasons_key(t.slice):
                    hits.append((fn, node.lineno))
        if isinstance(node, ast.Dict) and any(_is_reasons_key(k) for k in node.keys):
            hits.append((fn, node.lineno))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend", "insert"}
        ):
            target = node.func.value
            if isinstance(target, ast.Subscript) and _is_reasons_key(target.slice):
                hits.append((fn, node.lineno))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and node.args
            and _is_reasons_key(node.args[0])
        ):
            hits.append((fn, node.lineno))

    visit("<module>", tree)
    return hits


def _get_reasons_then_mutate(tree: ast.AST) -> list[tuple[str, int]]:
    """``r = v.get("reasons")`` followed by ``r.append(...)`` in one function."""
    hits: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {
            t.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "get"
            and n.value.args
            and _is_reasons_key(n.value.args[0])
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr in {"append", "extend", "insert"}
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id in bound
            ):
                hits.append((fn.name, n.lineno))
    return hits


def test_no_writer_bypasses_the_provenance_helpers():
    """A future writer that appends to ``reasons`` directly would be labelled
    model-authored. Enforced here rather than remembered."""
    offenders = []
    for path in sorted(AGENTS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn, line in _reasons_writes(tree) + _get_reasons_then_mutate(tree):
            if (path.relative_to(AGENTS).as_posix(), fn) not in _ALLOWED:
                offenders.append(f"{path.relative_to(AGENTS)}:{line} in {fn}")
    assert not offenders, (
        "write reasons via confidence.add_system_reason:\n" + "\n".join(offenders)
    )


# ── rendering ────────────────────────────────────────────────────────────


def _request(verdict: dict):
    from agents.handback.request import build_correction_request

    return build_correction_request(
        {"verdicts": [verdict]},
        None,
        {"aifactory": {"project_id": "p", "spec_id": "s"}},
    )


def _mixed_reject() -> dict:
    v = {"test_id": "t1", "verdict": "reject", "reasons": ["judge: wrong total"]}
    add_system_reason(v, "consistent test failure across 3 runs")
    return v


def test_handback_markdown_labels_each_line_and_drops_observed():
    from agents.handback.render import render_fix_request_md

    md = render_fix_request_md(_request(_mixed_reject()))
    assert "Observed:" not in md
    assert "**Measured:** consistent test failure across 3 runs" in md
    assert "LLM-authored" in md and "judge: wrong total" in md


def test_handback_json_is_additive():
    d = _request(_mixed_reject()).to_dict()
    failure = d["failing_tests"][0]
    # The old field is unchanged for AIFactory's current receiver...
    assert (
        failure["reason"] == "judge: wrong total; consistent test failure across 3 runs"
    )
    # ...and the provenance rides beside it.
    assert failure["reasons"] == [
        {"text": "judge: wrong total", "source": "model"},
        {"text": "consistent test failure across 3 runs", "source": "system"},
    ]
    assert "reason_provenance" in d


def test_a_fallback_reason_string_is_labelled_model():
    """No ``reasons`` list: ``reason`` is the judge's own fallback field."""
    from agents.handback.render import render_fix_request_md

    v = {"test_id": "t1", "verdict": "reject", "reason": "judge: off by one"}
    md = render_fix_request_md(_request(v))
    assert "LLM-authored, not verified):** judge: off by one" in md
    assert "**Measured:**" not in md


def test_no_reason_at_all_is_not_labelled_either_way():
    from agents.handback.render import render_fix_request_md

    md = render_fix_request_md(_request({"test_id": "t1", "verdict": "reject"}))
    assert "- **Reason:** (no reason recorded)" in md
    assert "LLM-authored, not verified):**" not in md

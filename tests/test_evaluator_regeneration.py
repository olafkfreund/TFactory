"""Exercise evaluator regeneration with local verdict files and stubbed sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import confidence, evaluator, gen_functional, regeneration
from agents.mutate_probe import MutationVerdict
from agents.stability_runner import StabilityVerdict

AC = "Return items in the order they were added"


def _row(test_id, verdict="reject", quote=None):
    row = {
        "test_id": test_id,
        "test_file": f"tests/test_{test_id}.py",
        "verdict": verdict,
        "reasons": ["original expectation contradicts criterion"],
        "signals_summary": {"stability": "stable", "mutation": "killed"},
        "semantic_relevance": "high",
    }
    if quote is not None:
        row["spec_contradiction"] = {"criterion": quote}
    return row


def _bundle(test_id, path, stability, mutation, failure_kind=None):
    return evaluator.EvaluatorSignals(
        test_id,
        path,
        target="module.py",
        rationale=AC,
        stability=SimpleNamespace(verdict=stability, failure_kind=failure_kind),
        mutation=SimpleNamespace(verdict=mutation) if mutation is not None else None,
    )


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    state = SimpleNamespace(
        ids=["original"],
        accepted=[],
        lane="unit",
        verdict="reject",
        votes=3,
        quotes=[None, None, None],
        crashed_first=None,
        second_verdict="accept",
        crashed_second=0,
        stability=StabilityVerdict.STABLE,
        assertion_failure=False,
        second_stability=StabilityVerdict.STABLE,
        second_mutation=MutationVerdict.KILLED,
        failure=None,
        calls=[],
        attempts=[],
        built=[],
        replacement_bundles=[],
        heads=[],
    )
    spec = tmp_path / "spec"
    project = tmp_path / "project"
    findings = spec / "findings"
    findings.mkdir(parents=True)
    (spec / "tests").mkdir()
    project.mkdir()
    verdicts = findings / "verdicts.json"
    state.findings = findings
    state.spec = spec
    state.verdicts = verdicts
    monkeypatch.setenv("TFACTORY_AUTO_TRIAGE", "0")

    original_attribution = evaluator._apply_lane_attribution
    original_enrichment = confidence.enrich_verdicts

    def capture_first(spec_dir, verdicts_path):
        original_attribution(spec_dir, verdicts_path)
        state.first = verdicts_path.read_bytes()

    def enrich(doc, *args):
        if state.failure == "enrich" and state.built:
            raise RuntimeError("second enrichment failed")
        return original_enrichment(doc, *args)

    def git_head(project_dir):
        assert project_dir == project
        state.heads.append(project_dir)
        if state.failure == "head_unreadable":
            return None
        if state.failure == "head_changed" and len(state.heads) == 2:
            return "different"
        return "original HEAD"

    async def judge(spec_dir, project_dir, prompt, verbose):
        assert json.loads((spec / "status.json").read_text())["status"] == "evaluating"
        call = len(state.calls)
        state.calls.append(prompt)
        verdicts.unlink(missing_ok=True)
        if call >= state.votes:
            assert "## REPLACEMENTS" in prompt
            assert "at least as strongly" in prompt
            if state.failure == "second_raise":
                raise RuntimeError("second vote failed after unlink")
            if state.failure == "cancel":
                raise asyncio.CancelledError
            if call - state.votes < state.crashed_second:
                verdicts.write_text('{"verdicts": []}')
                return None, "evaluator_session_error", "crashed"
            if state.failure == "file_removed_during_vote":
                (spec / "tests/test_b_regen1.py").unlink(missing_ok=True)
            rows = [
                _row(test_id, state.second_verdict)
                for test_id in state.replacement_bundles
                if not (state.failure == "omitted_vote" and test_id == "b-regen1")
            ]
        else:
            if call == state.crashed_first:
                return None, "evaluator_session_error", "crashed"
            rows = [
                _row(
                    tid,
                    "accept" if tid in state.accepted else state.verdict,
                    state.quotes[call],
                )
                for tid in state.ids
            ]
        doc = {"verdicts": rows}
        verdicts.write_text(json.dumps(doc))
        return doc, "", ""

    async def generate(spec_dir, subtask, brief, *, project_dir, verbose):
        test_id = subtask["id"]
        state.attempts.append(test_id)
        status = json.loads((spec / "status.json").read_text())
        assert status["status"] == "evaluating"
        assert status["phase"].startswith("evaluator_regenerating_")
        assert "assert original_source" in brief
        assert AC in brief
        assert "Never weaken" in brief
        if state.quotes[0]:
            assert f"Quoted AC: {state.quotes[0]}" in brief
        if state.failure == "generate_raise":
            raise RuntimeError("generator failed")
        if state.failure == "no_replacement" or (
            state.failure == "one_missing" and test_id == "b"
        ):
            return None
        test_file = gen_functional._regenerated_test_path(
            Path(subtask["files_to_create"][0])
        ).as_posix()
        assert not (spec / test_file).exists()
        if state.failure != "missing_file":
            (spec / test_file).write_text("# replacement")
        if state.failure == "sibling_generate_raise" and test_id == "b":
            raise RuntimeError("sibling generator failed after writing its file")
        replacement_id = test_id + "-regen1"
        return gen_functional.GeneratedTest(
            replacement_id,
            test_file,
            dict(subtask, id=replacement_id, files_to_create=[test_file]),
        )

    def build(spec_dir, project_dir, *lanes):
        assert spec_dir == spec and project_dir == project
        subtasks = [subtask for lane in lanes for subtask in lane]
        assert subtasks
        assert all(st["id"].endswith("-regen1") for st in subtasks)
        state.built.extend(st["id"] for st in subtasks)
        if state.failure == "build_raise":
            raise RuntimeError("signal runner failed")
        if state.failure == "no_bundles":
            return []
        bundles = [
            _bundle(
                st["id"],
                spec / st["files_to_create"][0],
                state.second_stability,
                state.second_mutation,
            )
            for st in subtasks
            if not (state.failure == "missing_bundle" and st["id"] == "b-regen1")
        ]
        state.replacement_bundles = [bundle.test_id for bundle in bundles]
        return bundles

    async def run():
        monkeypatch.setenv("TFACTORY_VERDICT_VOTES", str(state.votes))
        (spec / "status.json").write_text(json.dumps({"status": "evaluating"}))
        subtasks = [
            {
                "id": tid,
                "status": "completed",
                "lane": state.lane,
                "rationale": AC,
                "files_to_create": [_row(tid)["test_file"]],
            }
            for tid in state.ids
        ]
        (spec / "test_plan.json").write_text(
            json.dumps({"phases": [{"name": f"AC#1: {AC}", "subtasks": subtasks}]})
        )
        bundles = []
        for st in subtasks:
            path = spec / st["files_to_create"][0]
            path.write_text("assert original_source")
            bundles.append(
                _bundle(
                    st["id"],
                    path,
                    state.stability,
                    MutationVerdict.KILLED,
                    failure_kind="assertion" if state.assertion_failure else None,
                )
            )
        assert await evaluator._run_evaluator_session(spec, project, bundles, False)
        status = json.loads((spec / "status.json").read_text())
        assert status["status"] == "evaluated"
        assert status["tests_evaluated"] == status["verdicts_count"] == len(state.ids)
        return json.loads(verdicts.read_bytes())

    monkeypatch.setattr(evaluator, "_apply_lane_attribution", capture_first)
    monkeypatch.setattr(evaluator, "_git_head", git_head)
    monkeypatch.setattr(evaluator, "_judge_once", judge)
    monkeypatch.setattr(evaluator, "_build_all_bundles", build)
    monkeypatch.setattr(
        evaluator, "_maybe_run_build", lambda *_: pytest.fail("rebuilt the product")
    )
    monkeypatch.setattr(gen_functional, "regenerate_test", generate)
    monkeypatch.setattr(confidence, "enrich_verdicts", enrich)
    state.run = run
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("votes", [1, 3])
async def test_replaces_once_and_archives_raw_votes(scenario, votes):
    scenario.votes = votes
    replacement_path = scenario.spec / "tests/test_original_regen1.py"
    replacement_path.write_text("stale replacement")
    (scenario.findings / "verdicts.pass1.vote0.json").write_text("stale archive")
    result = await scenario.run()
    [row] = result["verdicts"]
    assert row["test_id"] == "original-regen1"
    assert row["test_file"] == "tests/test_original_regen1.py"
    assert row["lane"] == "unit"
    assert row["regenerated_from"] == "original"
    assert row["verdict"] == "accept"
    assert replacement_path.read_text() == "# replacement"
    [entry] = result["regenerated"]
    assert entry["original"] == json.loads(scenario.first)["verdicts"][0]
    assert entry["outcome"] == "replaced"
    assert entry["replacement_id"] == row["test_id"]
    assert entry["reason_kind"] == "test_quality"
    assert "quoted_ac" not in entry
    assert scenario.attempts == ["original"]
    assert scenario.built == ["original-regen1"]
    assert len(scenario.calls) == 2 * votes
    for i in range(votes):
        archive = scenario.findings / f"verdicts.pass1.vote{i}.json"
        if votes > 1:
            assert json.loads(archive.read_text()) == {"verdicts": [_row("original")]}
        else:
            assert not archive.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verdict", "stability", "mutation", "lane", "expected"),
    [
        ("reject", "stable", "killed", "unit", "reject"),
        ("accept", "flaky", "killed", "unit", "reject"),
        ("accept", "stable", "survived", "unit", "reject"),
        ("accept", "stable", "not_run", "unit", "reject"),
        ("accept", "stable", "not_run", "api", "flag"),
        ("accept", "stable", "no_mutation", "api", "reject"),
    ],
)
async def test_applies_replacement_gate(
    scenario, verdict, stability, mutation, lane, expected
):
    scenario.second_verdict = verdict
    scenario.second_stability = StabilityVerdict(stability)
    scenario.second_mutation = MutationVerdict(mutation)
    scenario.lane = lane
    result = await scenario.run()
    [row] = result["verdicts"]
    [entry] = result["regenerated"]
    assert row["verdict"] == expected
    if expected == "reject":
        assert row == entry["original"] == json.loads(scenario.first)["verdicts"][0]
        assert entry["outcome"] == "kept_reject"
        assert not (scenario.spec / "tests/test_original_regen1.py").exists()
    else:
        assert entry["outcome"] == "replaced"
        assert row["lane"] == "api"
        assert any("capped at flag" in reason for reason in row["reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason", "attempts"),
    [
        ("no_replacement", "no replacement generated", ["original"]),
        ("head_unreadable", "git HEAD unreadable", []),
    ],
)
async def test_no_second_vote_without_generation(scenario, failure, reason, attempts):
    scenario.failure = failure
    result = await scenario.run()
    assert result["verdicts"] == json.loads(scenario.first)["verdicts"]
    [entry] = result["regenerated"]
    assert entry["outcome"] == "kept_reject"
    assert entry["reason"] == reason
    assert entry["replacement_id"] is None
    assert len(scenario.calls) == scenario.votes
    assert scenario.attempts == attempts
    assert scenario.built == []
    assert not list(scenario.findings.glob("verdicts.pass1.vote*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "reason", "replacement_id"),
    [
        ("one_missing", "no replacement generated", None),
        ("missing_bundle", "replacement bundle missing", "b-regen1"),
        ("omitted_vote", "replacement verdict missing", "b-regen1"),
        ("file_removed_during_vote", "a selected replacement is missing", "b-regen1"),
    ],
)
async def test_incomplete_replacement_keeps_only_its_original(
    scenario, failure, reason, replacement_id
):
    scenario.ids = ["a", "b"]
    scenario.failure = failure
    result = await scenario.run()
    kept, replacement = result["verdicts"]
    assert kept == json.loads(scenario.first)["verdicts"][1]
    assert kept["test_id"] == "b"
    assert kept["verdict"] == "reject"
    assert replacement["test_id"] == "a-regen1"
    assert replacement["verdict"] == "accept"
    replaced_entry, kept_entry = result["regenerated"]
    assert replaced_entry["outcome"] == "replaced"
    assert kept_entry["original"] == kept
    assert kept_entry["outcome"] == "kept_reject"
    assert kept_entry["reason"] == reason
    assert kept_entry["replacement_id"] == replacement_id
    assert (scenario.spec / "tests/test_a_regen1.py").is_file()
    assert not (scenario.spec / "tests/test_b_regen1.py").exists()
    assert len(scenario.calls) == 2 * scenario.votes
    if failure == "missing_bundle":
        assert '"replacement_id": "b-regen1"' not in scenario.calls[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crashed", "test_id", "verdict", "outcome"),
    [
        (1, "original-regen1", "accept", "replaced"),
        (2, "original", "reject", "kept_reject"),
        (3, "original", "reject", "kept_reject"),
    ],
)
async def test_replacement_vote_counts_crashes_as_rejects(
    scenario, crashed, test_id, verdict, outcome
):
    scenario.crashed_second = crashed
    result = await scenario.run()
    [row] = result["verdicts"]
    [entry] = result["regenerated"]
    assert row["test_id"] == test_id
    assert row["verdict"] == verdict
    assert entry["outcome"] == outcome
    assert (scenario.spec / "tests/test_original_regen1.py").exists() == (
        outcome == "replaced"
    )
    assert len(scenario.calls) == 2 * scenario.votes
    if crashed == scenario.votes:
        assert entry["reason"] == "replacement vote failed"
    elif outcome == "kept_reject":
        assert entry["reason"] == "replacement was not judged accept or flag"


@pytest.mark.asyncio
async def test_no_replacement_bundles_keeps_raw_first_pass_votes(scenario):
    scenario.failure = "no_bundles"
    result = await scenario.run()
    assert result["verdicts"] == json.loads(scenario.first)["verdicts"]
    [entry] = result["regenerated"]
    assert entry["outcome"] == "kept_reject"
    assert entry["reason"] == "replacement bundle missing"
    assert len(scenario.calls) == scenario.votes
    for i in range(scenario.votes):
        vote = scenario.findings / f"verdicts.vote{i}.json"
        assert json.loads(vote.read_text()) == {"verdicts": [_row("original")]}
    assert not list(scenario.findings.glob("verdicts.pass1.vote*.json"))
    assert not list(scenario.spec.rglob("*_regen1*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "generate_raise",
        "sibling_generate_raise",
        "missing_file",
        "build_raise",
        "second_raise",
        "enrich",
        "invariant",
        "head_changed",
    ],
)
async def test_pass_failure_restores_first_pass_bytes(scenario, monkeypatch, failure):
    scenario.failure = failure
    if failure == "sibling_generate_raise":
        scenario.ids = ["a", "b"]
    if failure == "invariant":
        monkeypatch.setattr(
            regeneration, "check_merge_invariant", lambda *_: "violation"
        )
    await scenario.run()
    assert scenario.verdicts.read_bytes() == scenario.first
    assert not list(scenario.spec.rglob("*_regen1*"))
    if failure in ("second_raise", "enrich", "invariant"):
        assert len(scenario.calls) > scenario.votes
    if failure == "head_changed":
        assert len(scenario.heads) == 2
        assert len(scenario.calls) == scenario.votes


@pytest.mark.asyncio
async def test_cancellation_restores_first_pass_bytes(scenario):
    scenario.failure = "cancel"
    with pytest.raises(asyncio.CancelledError):
        await scenario.run()
    assert len(scenario.calls) > scenario.votes
    assert scenario.verdicts.read_bytes() == scenario.first
    assert not list(scenario.spec.rglob("*_regen1*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("quoting", ["split", "stale", "unanimous"])
async def test_product_failure_requires_current_unanimous_quoted_contradiction(
    scenario, quoting
):
    scenario.stability = StabilityVerdict.CONSISTENT_FAIL
    scenario.assertion_failure = True
    scenario.quotes = [AC, AC, None if quoting == "split" else AC]
    if quoting == "stale":
        scenario.crashed_first = 2
        stale = scenario.findings / "verdicts.vote2.json"
        stale.write_text(json.dumps({"verdicts": [_row("original", quote=AC)]}))
    scenario.second_verdict = "reject"
    result = await scenario.run()
    assert result["verdicts"] == json.loads(scenario.first)["verdicts"]
    assert result["verdicts"][0]["verdict"] == "reject"
    if quoting == "unanimous":
        assert scenario.attempts == ["original"]
        [entry] = result["regenerated"]
        assert entry["quoted_ac"] == AC
        assert entry["reason_kind"] == "spec_contradiction"
        assert entry["outcome"] == "kept_reject"
        assert AC in scenario.calls[-1]
    else:
        assert not scenario.attempts
        assert "regenerated" not in result


@pytest.mark.asyncio
async def test_generation_cap_and_stable_order(scenario):
    scenario.ids = ["e", "b", "d", "a", "c"]
    result = await scenario.run()
    assert scenario.attempts == ["a", "b", "c"]
    assert [e["original"]["test_id"] for e in result["regenerated"]] == list("abcde")
    for entry in result["regenerated"][3:]:
        assert entry["outcome"] == "kept_reject"
        assert entry["replacement_id"] is None
        assert entry["reason"] == "over the per-run regeneration cap (3)"
    assert [
        row["test_id"] for row in result["verdicts"] if row["verdict"] == "reject"
    ] == [
        "e",
        "d",
    ]


@pytest.mark.asyncio
async def test_no_rejects_leaves_first_pass_alone(scenario):
    scenario.verdict = "accept"
    result = await scenario.run()
    assert "regenerated" not in result
    assert not scenario.attempts
    assert not scenario.heads
    assert scenario.verdicts.read_bytes() == scenario.first
    assert len(list(scenario.findings.glob("verdicts.vote*.json"))) == scenario.votes
    assert not list(scenario.findings.glob("verdicts.pass1.vote*.json"))


@pytest.mark.asyncio
async def test_preserves_non_candidates_during_successful_merge(scenario):
    scenario.ids = ["original", "already-accepted"]
    scenario.accepted = ["already-accepted"]
    result = await scenario.run()
    assert scenario.attempts == ["original"]
    untouched = json.loads(scenario.first)["verdicts"][1]
    assert result["verdicts"][0] == untouched
    assert result["verdicts"][1]["regenerated_from"] == "original"


@pytest.mark.parametrize(
    ("row", "stability", "mutation", "lane", "expected"),
    [
        (None, "stable", "killed", "unit", (False, False)),
        (
            {"verdict": "accept", "judged": False},
            "stable",
            "killed",
            "unit",
            (False, False),
        ),
        ({"verdict": "reject"}, "stable", "killed", "unit", (False, False)),
        ({"verdict": "flag"}, "stable", "killed", "unit", (True, False)),
        ({"verdict": "accept"}, None, "killed", "unit", (False, False)),
        ({"verdict": "accept"}, "flaky", "killed", "unit", (False, False)),
        ({"verdict": "accept"}, "stable", "survived", "api", (False, False)),
        ({"verdict": "accept"}, "stable", "error", "api", (False, False)),
        ({"verdict": "accept"}, "stable", "unknown", "api", (False, False)),
        ({"verdict": "accept"}, "stable", "no_mutation", "api", (False, False)),
        ({"verdict": "accept"}, "stable", None, "api", (True, True)),
        ({"verdict": "flag"}, "stable", "not_run", "browser", (True, True)),
        ({"verdict": "accept"}, "stable", None, "functional", (False, False)),
        ({"verdict": "accept"}, "stable", "not_run", "", (False, False)),
        ({"verdict": "accept"}, "stable", "not_run", None, (False, False)),
        ({"verdict": "accept"}, "stable", "not_run", "unknown", (False, False)),
        ({"verdict": "accept"}, "stable", "killed", "unknown", (True, False)),
    ],
)
def test_replacement_gate(row, stability, mutation, lane, expected):
    bundle = _bundle("x", Path("unused"), stability, mutation)
    counts, cap, reason = regeneration.replacement_gate(row, bundle, lane)
    assert (counts, cap) == expected
    assert reason


def test_replacement_gate_requires_bundle():
    assert regeneration.replacement_gate({"verdict": "accept"}, None, "unit")[:2] == (
        False,
        False,
    )


def test_merge_invariant():
    original = _row("a")
    other = _row("b", "accept")
    replacement = _row("a-regen1", "flag")
    entry = {"original": original, "replacement_id": "a-regen1", "outcome": "replaced"}
    check = regeneration.check_merge_invariant
    assert check([original, other], [other, replacement], [entry]) is None
    assert check([original, other], [original, other], []) is None
    assert check([original, other], [other], [])
    assert check([original, other], [original, dict(other, reasons=[])], [])
    assert check([original, other], [dict(original, verdict="flag"), other], [])
    assert check([original, other], [original, other, replacement], [])
    assert check([original, other], [other], [entry])
    assert check(
        [original, other], [other, dict(replacement, verdict="reject")], [entry]
    )
    assert check([original, other], [other, replacement], [entry] * 4)
    assert check([original, other], [other, replacement, replacement], [entry])
    assert check([original, other], [other, replacement, original], [entry])

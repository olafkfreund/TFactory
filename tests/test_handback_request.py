"""Tests for the handback correction-request builder + renderer (P2 / #184).

Pure-compute: canned dicts in, CorrectionRequest + markdown out. No network,
no AIFactory, no LLM — exactly the seam the live send (P4) will wrap.
"""

from __future__ import annotations

from agents.handback import build_correction_request, render_fix_request_md
from agents.handback.request import CorrectionRequest

# A representative source.json (post-P1) carrying the aifactory envelope.
SOURCE = {
    "project_id": "demo",
    "spec_id": "001-login",
    "branch": "feature/login",
    "base_ref": "main",
    "correction_cycle": 0,
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    },
}

VERDICTS = {
    "verdicts": [
        {"test_id": "t_ok", "verdict": "accept", "reasons": ["covers the AC"]},
        {"test_id": "t_flag", "verdict": "flag", "reasons": ["minor smell"]},
        {
            "test_id": "t_bad",
            "verdict": "reject",
            "reasons": ["assertion failed: expected 200, got 500"],
            "lane": "api",
            "acceptance_criterion": "login returns 200 on valid creds",
        },
    ]
}

TRIAGE = {
    "rejected": [{"test_id": "t_bad", "test_file": "tests/test_login_api.py"}],
    "committed": [{"test_id": "t_ok", "test_file": "tests/test_login_ok.py"}],
}


# ── build_correction_request ─────────────────────────────────────────────


def test_selects_only_rejects() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    ids = [f.test_id for f in req.failures]
    assert ids == ["t_bad"]  # accept + flag excluded


# #1344: the harm this plan exists to prevent. A test that never reached the
# subject (classified "environment", reclassified not_run by
# apply_environment_override) must never ride alongside a genuine reject into
# AIFactory's hand-back. _FAILING_VERDICTS = frozenset({"reject"}) is what
# excludes not_run — not_run is already not a failure to the renderer or the
# never-overclaim gate; this is the one place that could silently widen and
# undo it.

# #1344: both entries start as `reject` — the environmental one is turned into
# `not_run` by enrich_verdicts, NOT hard-coded here. Hard-coding it was the
# original mistake: the test then passed with the entire change reverted,
# because it only re-tested _FAILING_VERDICTS. Found by independent review.
ENV_AND_REJECT_VERDICTS = {
    "verdicts": [
        {
            "test_id": "t_env",
            "verdict": "reject",
            "reasons": ["assertion failed"],
            "lane": "api",
            "signals_summary": {"stability": "consistent_fail"},
        },
        {
            "test_id": "t_bad",
            "verdict": "reject",
            "reasons": ["assertion failed: expected 200, got 500"],
            "lane": "api",
            "acceptance_criterion": "login returns 200 on valid creds",
            "signals_summary": {"stability": "consistent_fail"},
        },
    ]
}

ENV_AND_REJECT_TRIAGE = {
    "rejected": [
        {"test_id": "t_bad", "test_file": "tests/test_login_api.py"},
        {"test_id": "t_env", "test_file": "tests/test_delete_account.py"},
    ],
}


def test_environment_not_run_never_rides_with_a_genuine_reject() -> None:
    """A hand-back carries the genuine reject and NOT the unreached test.

    This is the property the whole issue exists for: a build agent must never
    be asked to fix code the lane never contacted.

    The verdict doc goes through the REAL path — `enrich_verdicts` with a
    `failure_kind: "environment"` for `t_env` — so the `not_run` it ends up with
    is produced by the change under test rather than written into the fixture.
    The first version of this test hard-coded `"verdict": "not_run"` and
    therefore passed with both production files reverted to origin/dev; it was
    testing `_FAILING_VERDICTS`, which `test_selects_only_rejects` already
    covers.
    """
    import copy

    from agents.confidence import enrich_verdicts

    doc = enrich_verdicts(
        copy.deepcopy(ENV_AND_REJECT_VERDICTS),
        failure_kind_by_test_id={"t_env": {"failure_kind": "environment"}},
    )
    # The override fired: this is the change under test doing the work.
    by_id = {v["test_id"]: v for v in doc["verdicts"]}
    assert by_id["t_env"]["verdict"] == "not_run", by_id["t_env"]
    assert by_id["t_bad"]["verdict"] == "reject", by_id["t_bad"]

    req = build_correction_request(doc, ENV_AND_REJECT_TRIAGE, SOURCE)

    # Count AND identity: "the environment one is absent" would also pass on an
    # empty hand-back.
    assert len(req.failures) == 1, req.failures
    assert req.failures[0].test_id == "t_bad", req.failures


def test_enriches_file_from_triage_and_maps_fields() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    f = req.failures[0]
    assert f.test_file == "tests/test_login_api.py"  # pulled from triage bucket
    assert f.lane == "api"
    assert f.verdict == "reject"
    assert f.acceptance_criterion == "login returns 200 on valid creds"
    assert "expected 200, got 500" in f.reason


def test_task_id_from_envelope() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    assert req.aifactory_task_id == "demo:001-login"
    assert req.source_kind == "triage"


def test_task_id_derived_when_envelope_lacks_it() -> None:
    src = {"aifactory": {"project_id": "p", "spec_id": "s"}}
    req = build_correction_request({"verdicts": []}, None, src)
    assert req.aifactory_task_id == "p:s"


def test_all_accept_is_nothing_to_hand_back() -> None:
    verdicts = {"verdicts": [{"test_id": "a", "verdict": "accept"}]}
    req = build_correction_request(verdicts, None, SOURCE)
    assert req.failures == []
    assert req.nothing_to_hand_back is True


def test_visual_plan_makes_request_non_empty_even_without_failures() -> None:
    req = build_correction_request(
        {"verdicts": []},
        None,
        SOURCE,
        visual_correction_plan="# Correction plan\n\nButton overlaps footer.",
    )
    assert req.failures == []
    assert req.nothing_to_hand_back is False
    assert req.source_kind == "visual_inspection"


def test_failures_sorted_by_test_id_for_determinism() -> None:
    verdicts = {
        "verdicts": [
            {"test_id": "z", "verdict": "reject", "reasons": ["x"]},
            {"test_id": "a", "verdict": "reject", "reasons": ["y"]},
        ]
    }
    req = build_correction_request(verdicts, None, SOURCE)
    assert [f.test_id for f in req.failures] == ["a", "z"]


def test_missing_reason_has_safe_fallback() -> None:
    verdicts = {"verdicts": [{"test_id": "t", "verdict": "reject"}]}
    req = build_correction_request(verdicts, None, SOURCE)
    assert req.failures[0].reason == "(no reason recorded)"


def test_to_dict_envelope_shape() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    d = req.to_dict()
    assert d["aifactory_task_id"] == "demo:001-login"
    assert d["aifactory"]["api_url"] == "http://localhost:3101"
    assert d["source"] == "triage"
    assert d["failing_tests"][0]["test_id"] == "t_bad"
    assert d["has_visual_plan"] is False


# ── render_fix_request_md ────────────────────────────────────────────────


def test_render_is_deterministic_and_complete() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    md = render_fix_request_md(req)
    assert render_fix_request_md(req) == md  # deterministic
    assert "# QA Fix Request — from TFactory" in md
    assert "`demo:001-login`" in md
    assert "**Failing tests:** 1" in md
    assert "t_bad" in md
    assert "tests/test_login_api.py" in md
    assert "lane: api" in md
    assert "login returns 200 on valid creds" in md
    assert "expected 200, got 500" in md


def test_render_includes_visual_plan_section() -> None:
    req = build_correction_request(
        {"verdicts": []},
        None,
        SOURCE,
        visual_correction_plan="Button overlaps the footer on mobile.",
    )
    md = render_fix_request_md(req)
    assert "## Visual inspection findings" in md
    assert "Button overlaps the footer on mobile." in md


def test_render_empty_request_still_well_formed() -> None:
    req = CorrectionRequest(aifactory=SOURCE["aifactory"])
    md = render_fix_request_md(req)
    assert "**Failing tests:** 0" in md
    assert "## Failures" not in md  # no failures section when there are none

"""A planner session that never reached a model must not be retried (#1011).

2026-08-07: the Claude credential was 61 days stale and unrefreshable. The
planner session returned WITHOUT error having done nothing, so the #854 guard —
which keys on ``status == "error"`` — missed it, the run fell through to "plan
missing -> retry", and a second identical session was dispatched into an auth
failure that could not succeed. The failure was then filed under
``planner_invalid_missing_after_retry``, blaming plan validity.

The trap these tests exist to pin: **zero tokens does NOT mean "never ran"**.
Ollama and the OpenAI-compatible endpoints report no token counts at all but DO
report which model served, so a token-based predicate would suppress the
legitimate retry on every local-model run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "backend"))

from agents.planner import _planning_reached_a_model  # noqa: E402


def _write_status(spec_dir: Path, workers: list[dict] | None) -> None:
    """Write a status.json whose workers match what ``_fold_worker`` persists.

    ``worker_id`` is not decoration: ``usage._workers_from_status`` re-indexes on
    it and DROPS any record without one, so a fixture that omits it tests a
    shape production never writes. (It did, first time round, and the test
    correctly failed.)
    """
    usage: dict = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    if workers is not None:
        usage["workers"] = [
            {**w, "worker_id": w.get("worker_id", w.get("phase", ""))}
            if isinstance(w, dict)
            else w
            for w in workers
        ]
    (spec_dir / "status.json").write_text(json.dumps({"usage": usage}))


def test_a_session_that_never_reached_a_model_is_detected(tmp_path: Path):
    # What record_in_status persists for a session that never started: the
    # requested id is known, the OBSERVED id is not.
    _write_status(
        tmp_path,
        [
            {
                "phase": "planning",
                "requested_model": "claude-opus-5",
                "model": "",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        ],
    )
    assert _planning_reached_a_model(tmp_path) is False


def test_a_LOCAL_model_run_with_zero_tokens_still_counts_as_run(tmp_path: Path):
    """The regression this whole design exists to avoid.

    Ollama reports no token counts. A "zero tokens means it never ran" check
    passes every other test in this file and breaks every local-model run.
    """
    _write_status(
        tmp_path,
        [
            {
                "phase": "planning",
                "requested_model": "ollama:qwen3:14b",
                "model": "qwen3:14b",  # observed — the provider named it
                "input_tokens": 0,  # ...and reported no tokens
                "output_tokens": 0,
            }
        ],
    )
    assert _planning_reached_a_model(tmp_path) is True


def test_a_normal_claude_run_counts_as_run(tmp_path: Path):
    _write_status(
        tmp_path,
        [
            {
                "phase": "planning",
                "requested_model": "claude-opus-5",
                "model": "claude-opus-5",
                "input_tokens": 1200,
                "output_tokens": 300,
            }
        ],
    )
    assert _planning_reached_a_model(tmp_path) is True


def test_evidence_from_another_phase_does_not_count(tmp_path: Path):
    # A coding session having reached a model says nothing about planning.
    _write_status(
        tmp_path,
        [{"phase": "coding", "requested_model": "x", "model": "x"}],
    )
    assert _planning_reached_a_model(tmp_path) is False


@pytest.mark.parametrize(
    "workers",
    [None, "not-a-list", [], ["not-a-dict"]],
    ids=["no-workers-key", "wrong-type", "empty", "junk-entry"],
)
def test_unreadable_evidence_behaves_exactly_as_today(tmp_path: Path, workers):
    """``unknown`` must not collapse into ``invalid``.

    When there is nothing to conclude from, the planner must retry exactly as it
    did before — inventing an auth failure from missing evidence is the original
    defect wearing a new hat (the distinction ``credential_validity`` draws,
    #858).
    """
    if workers == "not-a-list":
        (tmp_path / "status.json").write_text(
            json.dumps({"usage": {"workers": "not-a-list"}})
        )
    elif workers == ["not-a-dict"]:
        _write_status(tmp_path, ["not-a-dict"])  # type: ignore[list-item]
    else:
        _write_status(tmp_path, workers)
    assert _planning_reached_a_model(tmp_path) is True


def test_no_status_file_at_all_behaves_as_today(tmp_path: Path):
    assert _planning_reached_a_model(tmp_path) is True

"""A broken ruff must fail the ratchet, not read as "no violations".

ruff exits >=2 on its own failure (binary missing, config parse error) and
writes nothing to stdout. Treating that empty stdout as a clean file makes the
no-regression gate pass green on a linter that never ran -- the gate reports
success while measuring nothing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ratchet_lint  # noqa: E402


class _Res:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub(monkeypatch, res):
    monkeypatch.setattr(ratchet_lint, "_run", lambda *a, **k: res)


def test_ruff_own_failure_exits_rather_than_reporting_clean(monkeypatch):
    _stub(monkeypatch, _Res(2, stdout="", stderr="ruff: command not found"))
    with pytest.raises(SystemExit) as exc:
        ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py")
    assert exc.value.code == 2


def test_ruff_failure_surfaces_stderr_for_diagnosis(monkeypatch, capsys):
    _stub(monkeypatch, _Res(2, stdout="", stderr="invalid pyproject.toml"))
    with pytest.raises(SystemExit):
        ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py")
    assert "invalid pyproject.toml" in capsys.readouterr().err


def test_clean_file_still_counts_zero(monkeypatch):
    # Exit 0 with an empty JSON array is ruff saying "checked it, nothing wrong".
    _stub(monkeypatch, _Res(0, stdout="[]"))
    assert ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py") == {}


def test_violations_are_still_counted(monkeypatch):
    _stub(monkeypatch, _Res(1, stdout='[{"code": "E501"}, {"code": "E501"}]'))
    counts = ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py")
    assert counts["E501"] == 2


def test_ruff_writing_nothing_at_all_exits_rather_than_counting_zero(
    monkeypatch,
) -> None:
    """Factory#648: empty stdout was never the clean case.

    A clean run prints `[]`. Empty stdout on an exit-0 run is ruff having
    written no report, and the `return Counter()` that used to sit here counted
    it as perfection -- the same nothing-reads-as-clean defect Factory#590
    closed one exit code over, which `require_tool_ran` cannot reach because the
    process exited 0.

    This is the WIRING proof: that this fork routes its parse through
    `ratchet_helpers.ruff_findings` rather than restating it. No byte comparison
    can see a restatement, which is why the rule is also registered in the hub
    gate's _REQUIRED_RATCHET_RULES.
    """
    _stub(monkeypatch, _Res(0, stdout="   \n"))
    with pytest.raises(SystemExit) as exc:
        ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py")
    assert exc.value.code == 2


def test_ruff_output_that_is_not_json_exits_rather_than_counting_zero(
    monkeypatch, capsys
) -> None:
    # Factory#648: with `fix = true` reachable in a config ruff writes the FIXED
    # SOURCE to stdout and exits 0, so the parse would read Python as findings.
    # The canonical now says so; this used to be a bare `except` with no message.
    _stub(monkeypatch, _Res(0, stdout="import os\n\nx = 1\n"))
    with pytest.raises(SystemExit) as exc:
        ratchet_lint.ruff_counts("x = 1\n", "apps/backend/main.py")
    assert exc.value.code == 2
    assert "not the JSON finding list" in capsys.readouterr().err

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

"""A broken mypy must fail the ratchet, not read as "no errors".

The other half of #951. That PR fixed `ruff_counts()`; `mypy_errors()` has the
identical shape and was left behind. mypy exits 0 clean, 1 with errors, and 2 on
its own failure - missing config file, unrecognised argument, missing binary -
at which point the per-file error count comes back 0 and the base-vs-head
comparison reports "no regression" having measured nothing.

The guard is qualified with `count == 0` because mypy ALSO exits 2 on a BLOCKING
error (a syntax error in the file under test). That case still NAMES the file,
so the error is counted and gated on normally. Zero errors out of a failed run
is "did not run"; one or more is a real finding.

Refs PFactory#455, TFactory#951.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ratchet_lint  # noqa: E402

_PKG = "apps/backend"
_FILE = "apps/backend/agents/coder.py"


class _Res:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub(monkeypatch, res):
    monkeypatch.setattr(ratchet_lint.subprocess, "run", lambda *a, **k: res)


def _errors():
    return ratchet_lint.mypy_errors(_FILE, _PKG, "standards/mypy.ini")


@pytest.fixture(autouse=True)
def _at_repo_root(monkeypatch):
    # mypy_errors() resolves the package dir and relativises against it, so the
    # ratchet's own working directory (the repo root, as CI invokes it) is part
    # of what is under test.
    monkeypatch.chdir(Path(__file__).resolve().parents[1])


def test_mypy_own_failure_exits_rather_than_reporting_zero_errors(monkeypatch):
    _stub(monkeypatch, _Res(2, stderr="mypy: error: Cannot find config file"))
    with pytest.raises(SystemExit) as exc:
        _errors()
    assert exc.value.code == 2


def test_mypy_failure_surfaces_stderr_for_diagnosis(monkeypatch, capsys):
    _stub(monkeypatch, _Res(2, stderr="mypy: error: unrecognized arguments: --bogus"))
    with pytest.raises(SystemExit):
        _errors()
    assert "unrecognized arguments" in capsys.readouterr().err


def test_clean_file_still_counts_zero(monkeypatch):
    # Control: exit 0 with no error lines is a genuinely clean file.
    _stub(monkeypatch, _Res(0, stdout="Success: no issues found in 1 source file\n"))
    assert _errors() == 0


def test_blocking_error_is_counted_not_treated_as_a_crash(monkeypatch):
    # Control with teeth: a syntax error exits 2 as well, but names the file.
    # Keying the guard on the exit code alone would abort the ratchet here
    # instead of counting the error and gating on it.
    _stub(
        monkeypatch,
        _Res(2, stdout="agents/coder.py:1: error: Invalid syntax  [syntax]\n"),
    )
    assert _errors() == 1


def test_errors_are_still_counted(monkeypatch):
    # Control: exit 1 is the ordinary "found something" path, not a failure.
    out = (
        "agents/coder.py:3: error: Function is missing a type annotation  [no-untyped-def]\n"
        "agents/coder.py:9: error: Returning Any from function  [no-any-return]\n"
    )
    _stub(monkeypatch, _Res(1, stdout=out))
    assert _errors() == 2


def test_errors_in_other_files_are_not_this_file_s(monkeypatch):
    # Control: with exit 1 the run succeeded, so attributing nothing here is
    # honest - an error surfaced in an imported module belongs to that module.
    _stub(monkeypatch, _Res(1, stdout="agents/other.py:3: error: nope  [misc]\n"))
    assert _errors() == 0

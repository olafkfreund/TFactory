"""Tests for the CI-parity verification signal — issue #302.

The signal has two facets:
  - env_parity: did the lane grade under a CI-matching environment?
    (owned by DockerRunner.run_pytest; here we assert the result object
     reflects it)
  - real_imports: a static AST check — does the test import + exercise
    the subject, or only mock.patch() it out and assert against a fake?

Covered:
  - subject imported (with or without a collaborator patch) → REAL_IMPORT
  - subject only mocked, never imported → MOCKED_SUBJECT (flag)
  - @patch decorator form is detected
  - patch.object / mocker.patch forms are detected
  - path-shaped + file::symbol targets resolve to the right token
  - generic/stopword tokens never drive a flag
  - unresolved target → NO_REFERENCE
  - syntax error → ERROR (never crashes)
  - env_parity=False makes the result not-clean and status='no'
  - CIParityResult.status / is_clean / summary
"""

from __future__ import annotations

import textwrap

from agents.ci_parity import (
    CIParityResult,
    RealImportsVerdict,
    check_real_imports,
    compute_ci_parity,
)


def _src(text: str) -> str:
    return textwrap.dedent(text)


# ── REAL_IMPORT ─────────────────────────────────────────────────────────


def test_subject_imported_is_real_import():
    src = _src(
        """
        from app.auth import login
        def test_login():
            assert login("u", "p")
        """
    )
    r = compute_ci_parity(src, "app.auth.login")
    assert r.real_imports is RealImportsVerdict.REAL_IMPORT
    assert r.status == "yes"
    assert r.is_clean


def test_subject_imported_and_collaborator_patched_is_real_import():
    """The legitimate shape: import the subject, patch a *collaborator*.
    Must NOT be flagged as mocked-subject."""
    src = _src(
        """
        from app.billing import charge
        from unittest.mock import patch
        def test_charge():
            with patch("app.billing.stripe_client"):
                assert charge(10) == 10
        """
    )
    r = compute_ci_parity(src, "app.billing.charge")
    assert r.real_imports is RealImportsVerdict.REAL_IMPORT
    assert r.status == "yes"


# ── MOCKED_SUBJECT ──────────────────────────────────────────────────────


def test_subject_only_mocked_is_flagged():
    src = _src(
        """
        from unittest.mock import patch
        def test_login():
            with patch("app.auth.login", return_value=True) as m:
                assert m() is True
        """
    )
    r = compute_ci_parity(src, "app.auth.login")
    assert r.real_imports is RealImportsVerdict.MOCKED_SUBJECT
    assert r.status == "mocked-subject"
    assert not r.is_clean
    assert "app.auth.login" in r.mocked_targets


def test_decorator_patch_only_is_flagged():
    src = _src(
        """
        from unittest.mock import patch
        @patch("services.payment.process")
        def test_pay(m):
            m.return_value = 1
            assert m() == 1
        """
    )
    r = compute_ci_parity(src, "services/payment.py::process")
    assert r.real_imports is RealImportsVerdict.MOCKED_SUBJECT
    assert r.status == "mocked-subject"


def test_patch_object_form_is_detected():
    src = _src(
        """
        from unittest import mock
        def test_x():
            with mock.patch.object(some_mod, "widget"):
                pass
        """
    )
    verdict, subject, mocked, _ = check_real_imports(src, "widget")
    assert verdict is RealImportsVerdict.MOCKED_SUBJECT
    assert subject == "widget"


def test_mocker_patch_form_is_detected():
    src = _src(
        """
        def test_x(mocker):
            mocker.patch("inventory.reorder")
            assert True
        """
    )
    r = compute_ci_parity(src, "inventory.reorder")
    assert r.real_imports is RealImportsVerdict.MOCKED_SUBJECT


# ── conservative bias ───────────────────────────────────────────────────


def test_generic_stopword_target_never_flags():
    """A target like 'utils' is too generic to drive a flag even if a
    patch target happens to contain it."""
    src = _src(
        """
        from unittest.mock import patch
        def test_x():
            with patch("json.utils.dumps"):
                pass
        """
    )
    r = compute_ci_parity(src, "utils")
    assert r.real_imports is RealImportsVerdict.NO_REFERENCE
    assert r.status == "yes"


def test_unresolved_target_is_no_reference():
    r = compute_ci_parity("def test(): assert 1", None)
    assert r.real_imports is RealImportsVerdict.NO_REFERENCE
    assert r.status == "yes"


def test_subject_neither_imported_nor_patched_is_no_reference():
    src = _src(
        """
        def test_unrelated():
            assert 2 + 2 == 4
        """
    )
    r = compute_ci_parity(src, "app.payments.refund")
    assert r.real_imports is RealImportsVerdict.NO_REFERENCE


# ── robustness ──────────────────────────────────────────────────────────


def test_syntax_error_is_error_not_crash():
    r = compute_ci_parity("def (:bad", "app.x.y")
    assert r.real_imports is RealImportsVerdict.ERROR
    assert "did not parse" in r.reason


def test_env_parity_false_is_not_clean():
    src = "from app.auth import login\ndef test(): assert login()"
    r = compute_ci_parity(src, "app.auth.login", env_parity=False)
    assert r.status == "no"
    assert not r.is_clean


def test_result_summary_and_fields():
    r = CIParityResult(
        env_parity=True,
        real_imports=RealImportsVerdict.REAL_IMPORT,
        target_module="login",
    )
    assert "env-parity" in r.summary()
    assert "real_import" in r.summary()
    assert r.is_clean

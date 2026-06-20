"""RFC-0015 §4 D2 — the requirement -> test -> VAL traceability matrix.

TFactory already computes the two halves of this matrix internally:

  - the **AC -> test** mapping, in :func:`agents.ac_fidelity.build_ac_ledger`
    (every acceptance criterion -> the test(s) that cover it + each test's
    accept/flag/reject verdict); and
  - the **VAL level** the run actually reached, in
    :func:`agents.val_block.build_verification_block` (the gate-recomputed
    ``achieved_level``, never overclaimed).

D2 surfaces them together as a first-class ``verification.traceability[]`` block
(``apis/task-contract.schema.json`` ``$defs.verification.traceability``): one row
per AC -> its covering test ids/paths -> the VAL level achieved -> a status of
``passed | failed | not_run | skipped``. The CFactory matrix view (#126) renders
it as AC x test x VAL x verdict.

This module is pure (no I/O); the triager builds the inputs and attaches the
result to the verification block it already emits. It degrades gracefully: an AC
with no mapped test yields ``tests: []`` + ``status: not_run`` (an honest
traceability gap, never hidden), and a missing/empty ledger yields ``[]``.

Status mapping (ledger AC grade -> schema status), honest by construction:

  - ``verified``      -> ``passed``   (>=1 accepted test for the AC)
  - ``flagged_only``  -> ``skipped``  (covered, but only flagged -> needs review,
                                       NOT a clean pass)
  - ``unverified`` with tests -> ``failed``  (every covering test rejected)
  - ``unverified`` with no tests -> ``not_run`` (no test covers this AC)
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_traceability"]

_VAL0 = "VAL-0"


def _row_status(ac_status: str, has_tests: bool) -> str:
    """Map a ledger AC grade to a schema traceability status.

    Conservative + honest: an unknown grade with tests is reported ``failed``
    (not silently passed); without tests it is ``not_run`` (a coverage gap).
    """
    if ac_status == "verified":
        return "passed"
    if ac_status == "flagged_only":
        return "skipped"
    # "unverified" (or any unknown grade): tests that all rejected -> failed;
    # no covering test at all -> not_run (the traceability gap D2 must surface).
    return "failed" if has_tests else "not_run"


def _test_refs(tests: list[dict[str, Any]]) -> list[str]:
    """Test ids/paths covering an AC, preferring the file path then the id.

    The schema's example is ``tests/test_login.py::test_ok`` (file::id); we emit
    ``<test_file>::<test_id>`` when both are known, else whichever is present.
    De-duplicated, order-preserving. Empty entries are dropped.
    """
    refs: list[str] = []
    for t in tests or []:
        if not isinstance(t, dict):
            continue
        test_id = str(t.get("test_id") or "").strip()
        test_file = str(t.get("test_file") or "").strip()
        ref = (
            f"{test_file}::{test_id}"
            if (test_file and test_id)
            else (test_file or test_id)
        )
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def build_traceability(
    ledger: dict[str, Any] | None,
    verification_block: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the ``verification.traceability[]`` rows from the AC ledger + VAL block.

    ``ledger`` is :func:`agents.ac_fidelity.build_ac_ledger`'s output (its
    ``acceptance[]`` carries ``{ac_id, text, status, tests[]}``). ``verification_block``
    is :func:`agents.val_block.build_verification_block`'s output; its
    gate-recomputed ``achieved_level`` is the VAL the run truly reached.

    Returns one row per AC: ``{ac_id, ac_text?, tests[], val_level, status}``.
    A passed AC reaches the run's ``achieved_level``; an AC that is failed,
    skipped, or has no covering test is honestly pinned at ``VAL-0`` (its tests
    did not establish assurance). Never raises; an absent ledger -> ``[]``.
    """
    if not isinstance(ledger, dict):
        return []
    acceptance = ledger.get("acceptance")
    if not isinstance(acceptance, list):
        return []

    achieved = _VAL0
    if isinstance(verification_block, dict):
        achieved = str(verification_block.get("achieved_level") or _VAL0)

    rows: list[dict[str, Any]] = []
    for ac in acceptance:
        if not isinstance(ac, dict):
            continue
        ac_id = str(ac.get("ac_id") or "").strip()
        if not ac_id:
            continue
        raw_tests = ac.get("tests")
        tests: list[dict[str, Any]] = raw_tests if isinstance(raw_tests, list) else []
        refs = _test_refs(tests)
        status = _row_status(str(ac.get("status") or ""), bool(refs))
        # Honest VAL attribution: only a passed AC inherits the run's achieved
        # level; everything else (failed / skipped / uncovered) is VAL-0 — its
        # tests did not establish assurance, so it must not borrow the ceiling.
        val_level = achieved if status == "passed" else _VAL0
        row: dict[str, Any] = {
            "ac_id": ac_id,
            "tests": refs,
            "val_level": val_level,
            "status": status,
        }
        text = str(ac.get("text") or "").strip()
        if text:
            row["ac_text"] = text
        rows.append(row)
    return rows

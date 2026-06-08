"""Tests for assertion pinning across handback cycles (#283).

The core guarantee: a re-generated suite may only *add* assertions; dropping or
loosening a pinned assertion is flagged, not silently accepted.
"""

from __future__ import annotations

from pathlib import Path

from agents.handback.assertion_manifest import (
    check_drift,
    compute_manifest,
    diff_manifest,
    pin_manifest,
    read_pinned_manifest,
)

_PY = "def test_login():\n    assert resp.status == 200\n    assert resp.json()['ok'] is True\n"


def _write(d: Path, name: str, body: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)


def test_compute_manifest_counts_python_assertions(tmp_path: Path):
    _write(tmp_path, "test_a.py", _PY)
    m = compute_manifest(tmp_path)
    assert m["files"]["test_a.py"]["count"] == 2
    assert isinstance(m["manifest_hash"], str) and m["manifest_hash"]


def test_manifest_hash_stable_across_reformatting(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def test_x():\n    assert a == 1\n")
    h1 = compute_manifest(tmp_path)["manifest_hash"]
    # Reformat (extra spaces/blank lines) — same assertion, same hash.
    _write(tmp_path, "test_a.py", "def test_x():\n\n    assert a  ==  1\n")
    h2 = compute_manifest(tmp_path)["manifest_hash"]
    assert h1 == h2


def test_manifest_hash_changes_when_assertion_changes(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def test_x():\n    assert a == 1\n")
    h1 = compute_manifest(tmp_path)["manifest_hash"]
    _write(
        tmp_path, "test_a.py", "def test_x():\n    assert a == 2\n"
    )  # loosened/changed
    assert compute_manifest(tmp_path)["manifest_hash"] != h1


def test_counts_unittest_and_self_assert(tmp_path: Path):
    body = "class T:\n    def test(self):\n        self.assertEqual(a, b)\n        self.assertTrue(c)\n"
    _write(tmp_path, "test_u.py", body)
    assert compute_manifest(tmp_path)["files"]["test_u.py"]["count"] == 2


def test_counts_js_tokens(tmp_path: Path):
    _write(
        tmp_path,
        "x.test.ts",
        "it('x', () => { expect(a).toBe(1); expect(b).toEqual(2); })",
    )
    assert compute_manifest(tmp_path)["files"]["x.test.ts"]["count"] == 2


def test_diff_allows_additive(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def t():\n    assert a == 1\n")
    pinned = compute_manifest(tmp_path)
    _write(
        tmp_path, "test_a.py", "def t():\n    assert a == 1\n    assert b == 2\n"
    )  # added
    _write(tmp_path, "test_new.py", "def t():\n    assert z\n")  # new file
    report = diff_manifest(pinned, compute_manifest(tmp_path))
    assert report.ok is True
    assert report.violations == []


def test_diff_flags_dropped_assertion(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def t():\n    assert a == 1\n    assert b == 2\n")
    pinned = compute_manifest(tmp_path)
    _write(tmp_path, "test_a.py", "def t():\n    assert a == 1\n")  # dropped one
    report = diff_manifest(pinned, compute_manifest(tmp_path))
    assert report.ok is False
    assert report.violations[0].path == "test_a.py"
    assert report.violations[0].kind == "assertions_dropped"


def test_diff_flags_loosened_assertion(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def t():\n    assert status == 200\n")
    pinned = compute_manifest(tmp_path)
    _write(
        tmp_path, "test_a.py", "def t():\n    assert status == 500\n"
    )  # loosened bar
    report = diff_manifest(pinned, compute_manifest(tmp_path))
    assert report.ok is False  # changed hash → pinned assertion no longer present


def test_diff_flags_removed_file(tmp_path: Path):
    _write(tmp_path, "test_a.py", "def t():\n    assert a\n")
    pinned = compute_manifest(tmp_path)
    (tmp_path / "test_a.py").unlink()
    report = diff_manifest(pinned, compute_manifest(tmp_path))
    assert report.ok is False
    assert report.violations[0].kind == "file_removed"


# ── pin / read / check_drift against a spec dir ──────────────────────────────


def test_pin_is_idempotent(tmp_path: Path):
    spec = tmp_path / "spec"
    tests = tmp_path / "spec" / "tests"
    _write(tests, "test_a.py", "def t():\n    assert a == 1\n")
    first = pin_manifest(spec, tests)
    # Weaken the suite, then pin again — the pinned bar must NOT move.
    _write(tests, "test_a.py", "def t():\n    pass\n")
    second = pin_manifest(spec, tests)
    assert second["manifest_hash"] == first["manifest_hash"]
    assert read_pinned_manifest(spec)["manifest_hash"] == first["manifest_hash"]


def test_check_drift_ok_when_nothing_pinned(tmp_path: Path):
    spec = tmp_path / "spec"
    tests = tmp_path / "spec" / "tests"
    _write(tests, "test_a.py", "def t():\n    assert a\n")
    assert check_drift(spec, tests).ok is True  # no manifest pinned → gate off


def test_check_drift_rejects_weakened_rerun(tmp_path: Path):
    spec = tmp_path / "spec"
    tests = tmp_path / "spec" / "tests"
    _write(tests, "test_a.py", "def t():\n    assert a == 1\n    assert b == 2\n")
    pin_manifest(spec, tests)  # bar pinned on first failure
    _write(tests, "test_a.py", "def t():\n    assert a == 1\n")  # regenerated, weaker
    report = check_drift(spec, tests)
    assert report.ok is False
    assert report.violations[0].kind == "assertions_dropped"

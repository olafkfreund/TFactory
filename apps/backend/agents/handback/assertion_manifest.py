"""Assertion pinning for the bounded handback loop (#283).

``agents/handback/rerun.py`` resets a task to ``pending`` and re-fires the
Planner → Gen-Functional, which **regenerates** the test suite every cycle.
Assertions can therefore drift or weaken between rounds non-deterministically and
mask an unfixed bug — even though AIFactory's fixer never touches TFactory's
tests. This module pins the assertion set across cycles so each round provably
tests against the same bar.

The mechanism:

  1. On the first failure, snapshot the generated suite to an **assertion
     manifest** — a per-file set of normalized per-assertion hashes — stored at
     ``findings/assertion_manifest.json`` with a stable ``manifest_hash``.
  2. On a handback re-run, diff the regenerated suite against the pinned
     manifest: it may only **add** assertions. A dropped or *loosened* assertion
     (its normalized hash changes, so the pinned hash is no longer present) is a
     violation — the cycle is rejected, not silently accepted.
  3. The ``manifest_hash`` rides on the handback triage contract so CFactory can
     surface "round N ran the same assertions as round 1".

Python assertions are extracted structurally via ``ast`` (``assert`` statements +
``unittest`` ``self.assertX(...)`` calls), normalized by un-parsing so formatting
churn doesn't change a hash. Non-Python suites fall back to a language-spanning
assertion-token line extraction. Stdlib-only; never raises into the pipeline.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MANIFEST_NAME",
    "AssertionViolation",
    "DriftReport",
    "check_drift",
    "compute_manifest",
    "diff_manifest",
    "manifest_hash",
    "pin_manifest",
    "read_pinned_manifest",
]

MANIFEST_NAME = "assertion_manifest.json"

_TEST_GLOBS = (
    "**/test_*.py",
    "**/*_test.py",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.test.js",
    "**/*.test.jsx",
    "**/*.spec.ts",
    "**/*.spec.tsx",
    "**/*.spec.js",
    "**/*.spec.jsx",
)
_PRUNE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
    }
)

# Fallback assertion tokens for non-Python suites (jest/vitest/chai + node).
# Stop at ``;`` so semicolon-separated assertions on one line count separately.
_TOKEN_RE = re.compile(
    r"\bexpect\s*\([^;\n]*|\bassert\s*\([^;\n]*|\bassert\.\w+\s*\([^;\n]*"
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _python_assertions(source: str) -> list[str]:
    """Normalized fingerprints of every assertion in a Python test source.

    Uses the AST so whitespace/quote churn doesn't change a fingerprint: an
    ``assert`` node or a ``self.assert*`` / bare ``assert*`` call is un-parsed to
    canonical source and hashed. Returns ``[]`` on a syntax error (a half-written
    regeneration) — the caller treats "no assertions parsed" conservatively.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            out.append(_hash(ast.unparse(node.test)))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else fn.id
                if isinstance(fn, ast.Name)
                else ""
            )
            if name.startswith("assert") and name != "assert":
                out.append(_hash(ast.unparse(node)))
    return out


def _token_assertions(source: str) -> list[str]:
    """Fallback for non-Python suites: hash each assertion-token line."""
    return [_hash(re.sub(r"\s+", " ", m.strip())) for m in _TOKEN_RE.findall(source)]


def _assertions_for(path: Path, source: str) -> list[str]:
    return (
        _python_assertions(source)
        if path.suffix == ".py"
        else _token_assertions(source)
    )


def compute_manifest(tests_dir: Path | str) -> dict:
    """Build the assertion manifest for every test file under ``tests_dir``.

    Returns ``{"files": {relpath: {"assertions": [hash, ...], "count": n}},
    "manifest_hash": "<sha>"}``. ``manifest_hash`` is stable for a byte-stable
    (or merely reformatted) suite and changes when any assertion is added,
    dropped, or loosened.
    """
    base = Path(tests_dir)
    files: dict[str, dict] = {}
    if base.is_dir():
        seen: set[str] = set()
        for pattern in _TEST_GLOBS:
            for fp in sorted(base.glob(pattern)):
                if not fp.is_file() or _PRUNE_DIRS & set(fp.relative_to(base).parts):
                    continue
                rel = fp.relative_to(base).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    src = fp.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                hashes = sorted(_assertions_for(fp, src))
                files[rel] = {"assertions": hashes, "count": len(hashes)}
    return {"files": files, "manifest_hash": _manifest_hash(files)}


def _manifest_hash(files: dict) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return _hash(canonical)


def manifest_hash(manifest: dict) -> str:
    """The stable hash of a manifest (recomputed from its files for safety)."""
    return _manifest_hash(manifest.get("files", {}))


def _manifest_path(spec_dir: Path | str) -> Path:
    return Path(spec_dir) / "findings" / MANIFEST_NAME


def read_pinned_manifest(spec_dir: Path | str) -> dict | None:
    """The pinned manifest for a spec, or ``None`` if none has been pinned."""
    try:
        return json.loads(_manifest_path(spec_dir).read_text())
    except (OSError, ValueError):
        return None


def pin_manifest(
    spec_dir: Path | str, tests_dir: Path | str, *, force: bool = False
) -> dict:
    """Pin the current suite as the bar for this spec, if not already pinned.

    Idempotent: once pinned, later cycles read the same manifest (so the bar
    can't drift) unless ``force`` is set. Returns the effective manifest.
    Best-effort write — never raises into the pipeline.
    """
    existing = read_pinned_manifest(spec_dir)
    if existing is not None and not force:
        return existing
    manifest = compute_manifest(tests_dir)
    path = _manifest_path(spec_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2))
    except OSError:
        pass
    return manifest


@dataclass(frozen=True)
class AssertionViolation:
    path: str
    kind: str  # "file_removed" | "assertions_dropped"
    dropped: int = 0

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "dropped": self.dropped}


@dataclass(frozen=True)
class DriftReport:
    ok: bool
    violations: list[AssertionViolation] = field(default_factory=list)
    pinned_hash: str | None = None
    current_hash: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": [v.to_dict() for v in self.violations],
            "pinned_hash": self.pinned_hash,
            "current_hash": self.current_hash,
        }


def diff_manifest(pinned: dict, current: dict) -> DriftReport:
    """Additive-only gate: ``current`` must keep every pinned assertion.

    A pinned file that vanished, or any pinned assertion hash absent from the
    current suite (dropped *or* loosened — loosening changes the hash), is a
    violation. New files and new assertions are fine.
    """
    pinned_files = pinned.get("files", {})
    current_files = current.get("files", {})
    violations: list[AssertionViolation] = []
    for rel, pdata in pinned_files.items():
        cdata = current_files.get(rel)
        if cdata is None:
            violations.append(
                AssertionViolation(
                    rel, "file_removed", len(pdata.get("assertions", []))
                )
            )
            continue
        pinned_set = set(pdata.get("assertions", []))
        current_set = set(cdata.get("assertions", []))
        missing = pinned_set - current_set
        if missing:
            violations.append(
                AssertionViolation(rel, "assertions_dropped", len(missing))
            )
    return DriftReport(
        ok=not violations,
        violations=violations,
        pinned_hash=manifest_hash(pinned),
        current_hash=manifest_hash(current),
    )


def check_drift(spec_dir: Path | str, tests_dir: Path | str) -> DriftReport:
    """Diff the current suite under ``tests_dir`` against the pinned manifest.

    Returns ``ok=True`` (no violations) when nothing is pinned yet — the gate
    only engages once a bar exists, so first-run generation is never blocked.
    """
    pinned = read_pinned_manifest(spec_dir)
    if pinned is None:
        return DriftReport(ok=True)
    return diff_manifest(pinned, compute_manifest(tests_dir))

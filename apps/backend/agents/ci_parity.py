"""CI-parity verification signal — issue #302 (Hermes-inspired).

Generated tests can pass against mocks or a developer-shaped environment
yet fail in CI — *green that lies*. TFactory's job is **trustworthy**
verdicts, so the grading environment should mirror CI as closely as
possible. This module supplies the sixth Evaluator signal: **CI parity**.

Two facets, one result:

1. **Environment parity** — the lane runner grades in an environment that
   matches CI: ambient credentials blanked, timezone forced to UTC, hash
   seed pinned, locale normalised, on top of the existing
   ``--network=none --read-only`` Docker sandbox. The runner owns the env
   construction (``tools.runners.docker_runner.ci_parity_env``); this
   module only records whether the test was graded under it
   (``env_parity``).

2. **Real-imports** — a *static* check, modelled on Hermes' rule:

       "Before wiring an unused module into a live code path, E2E test the
        real resolution chain with actual imports — not mocks."

   We penalise/flag a suite whose passing depends on **mocking out the
   subject module under test** rather than importing and exercising it.
   A test that only ever ``mock.patch("<subject>")`` and never imports it
   is asserting against a fake — the verdict should say so.

The result is rendered into the Evaluator CONTEXT block and echoed into
``verdicts.json``'s ``signals_summary.ci_parity`` so the Triager can carry
it into the triage report.

**Conservative bias** (like ``lint_promotion``): we only declare
``MOCKED_SUBJECT`` when we positively find the subject patched *and never
really imported*. When the subject is both imported and patched (the
common, legitimate "patch a collaborator" shape), we treat it as a real
import. When we can't resolve the subject at all, we stay silent
(``NO_REFERENCE``) rather than guess.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum


class RealImportsVerdict(str, Enum):
    """Outcome of the static real-imports check for one test."""

    REAL_IMPORT = "real_import"  # subject is imported (good)
    MOCKED_SUBJECT = "mocked_subject"  # subject only mocked, never imported (flag)
    NO_REFERENCE = "no_reference"  # subject not resolvable / not referenced
    ERROR = "error"  # source didn't parse


@dataclass(frozen=True)
class CIParityResult:
    """Per-test CI-parity signal bundle.

    Args:
        env_parity: True when the test was (or will be) graded under the
            CI-parity runner env (creds blanked + UTC + isolation).
        real_imports: The ``RealImportsVerdict``.
        target_module: The subject module/token resolved from the
            subtask's ``target`` (best-effort; None when unresolvable).
        mocked_targets: Patch targets found in the source that match the
            subject (the evidence behind a ``MOCKED_SUBJECT`` verdict).
        reason: Short human-readable justification.
    """

    env_parity: bool
    real_imports: RealImportsVerdict
    target_module: str | None = None
    mocked_targets: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def is_clean(self) -> bool:
        """True when the grade is CI-trustworthy: parity env AND the
        subject is not mocked-out."""
        return (
            self.env_parity and self.real_imports != RealImportsVerdict.MOCKED_SUBJECT
        )

    @property
    def status(self) -> str:
        """Compact status for the verdict/triage report.

        - ``"yes"``            — parity env, subject not mocked-out
        - ``"mocked-subject"`` — subject is patched but never imported (flag)
        - ``"no"``             — graded without the parity env
        """
        if not self.env_parity:
            return "no"
        if self.real_imports == RealImportsVerdict.MOCKED_SUBJECT:
            return "mocked-subject"
        return "yes"

    def summary(self) -> str:
        env = "env-parity" if self.env_parity else "no-env-parity"
        return f"{env}, real_imports={self.real_imports.value}"


# ─── Subject resolution ─────────────────────────────────────────────────


def _subject_tokens(target: str | None) -> set[str]:
    """Best-effort set of identifier tokens naming the subject under test.

    ``target`` is the planner's free-form pointer at the thing the test
    covers — it may be a dotted path (``app.auth.login``), a file path
    (``apps/backend/auth.py``), a ``file::symbol`` form, or just a symbol.
    We extract every token that could plausibly appear as a component of a
    ``mock.patch("...")`` dotted target or an import name.
    """
    if not target:
        return set()
    raw = target.strip()
    # Drop a "::symbol" / "#symbol" suffix — keep the module locator.
    for sep in ("::", "#"):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
    raw = raw.strip()
    tokens: set[str] = set()
    if "/" in raw or raw.endswith(".py"):
        # Path-shaped: take the file stem (last path component sans .py).
        stem = raw.rstrip("/").split("/")[-1]
        if stem.endswith(".py"):
            stem = stem[:-3]
        if stem and stem != "__init__":
            tokens.add(stem)
    elif "." in raw:
        # Dotted module path: keep the full dotted form + each component.
        tokens.add(raw)
        tokens.update(part for part in raw.split(".") if part)
    elif raw:
        tokens.add(raw)
    # Never let trivial/ambiguous tokens drive a flag.
    return {t for t in tokens if len(t) > 1 and t not in _STOPWORD_TOKENS}


# Tokens too generic to be a reliable subject match (avoid false positives).
_STOPWORD_TOKENS = frozenset(
    {
        "app",
        "apps",
        "src",
        "lib",
        "test",
        "tests",
        "main",
        "core",
        "utils",
        "util",
        "common",
        "base",
        "init",
        "module",
        "api",
        "backend",
        "frontend",
    }
)


# ─── AST scan ───────────────────────────────────────────────────────────


class _RealImportsVisitor(ast.NodeVisitor):
    """Collect imported module/symbol names and mock.patch targets."""

    def __init__(self) -> None:
        self.imported: set[str] = set()  # module + symbol names imported
        self.patch_targets: list[str] = []  # string targets of patch(...)

    # -- imports --
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imported.add(alias.name)
            self.imported.update(p for p in alias.name.split(".") if p)
            if alias.asname:
                self.imported.add(alias.asname)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imported.add(node.module)
            self.imported.update(p for p in node.module.split(".") if p)
        for alias in node.names:
            self.imported.add(alias.name)
            if alias.asname:
                self.imported.add(alias.asname)
        self.generic_visit(node)

    # -- mock.patch("...") in calls and decorators --
    def visit_Call(self, node: ast.Call) -> None:
        if self._is_patch_call(node.func):
            tgt = self._first_str_arg(node)
            if tgt:
                self.patch_targets.append(tgt)
        self.generic_visit(node)

    @staticmethod
    def _is_patch_call(func: ast.expr) -> bool:
        """Match ``patch(...)``, ``mock.patch(...)``, ``patch.object(...)``,
        ``mocker.patch(...)`` — anything whose call chain ends in ``patch``
        or ``patch.object``."""
        if isinstance(func, ast.Name):
            return func.id == "patch"
        if isinstance(func, ast.Attribute):
            if func.attr == "patch":
                return True
            # patch.object(...) — func is Attribute(attr="object",
            # value=Attribute/Name ending in "patch")
            if func.attr in ("object", "dict", "multiple"):
                inner = func.value
                if isinstance(inner, ast.Name):
                    return inner.id == "patch"
                if isinstance(inner, ast.Attribute):
                    return inner.attr == "patch"
        return False

    @staticmethod
    def _first_str_arg(node: ast.Call) -> str | None:
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        return None


def _patch_components(target: str) -> set[str]:
    """Dotted components of a patch target string, plus the full string."""
    comps = {p for p in target.split(".") if p}
    comps.add(target)
    return comps


def check_real_imports(
    source: str,
    target: str | None,
) -> tuple[RealImportsVerdict, str | None, tuple[str, ...], str]:
    """Static analysis: is the subject imported, or only mocked out?

    Returns ``(verdict, resolved_subject, mocked_targets, reason)``.

    Decision (conservative):
      - subject token matches an import → ``REAL_IMPORT`` (even if also
        patched — that's the legitimate "patch a collaborator" shape).
      - else subject token matches a ``patch(...)`` target → ``MOCKED_SUBJECT``.
      - else → ``NO_REFERENCE``.
      - unparseable source → ``ERROR``.
    """
    tokens = _subject_tokens(target)
    if not tokens:
        return (
            RealImportsVerdict.NO_REFERENCE,
            None,
            (),
            "subject not resolvable from target",
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return (
            RealImportsVerdict.ERROR,
            None,
            (),
            f"source did not parse: {exc.msg}",
        )

    v = _RealImportsVisitor()
    v.visit(tree)

    # Did the test really import the subject?
    imported_match = sorted(tokens & v.imported)
    if imported_match:
        return (
            RealImportsVerdict.REAL_IMPORT,
            imported_match[0],
            (),
            f"subject {imported_match[0]!r} is imported",
        )

    # Is the subject patched out without ever being imported?
    mocked: list[str] = []
    subject_hit: str | None = None
    for tgt in v.patch_targets:
        comps = _patch_components(tgt)
        overlap = tokens & comps
        if overlap:
            mocked.append(tgt)
            if subject_hit is None:
                subject_hit = sorted(overlap)[0]
    if mocked:
        return (
            RealImportsVerdict.MOCKED_SUBJECT,
            subject_hit,
            tuple(mocked),
            (
                f"subject {subject_hit!r} is mocked out "
                f"({', '.join(mocked)}) but never imported"
            ),
        )

    return (
        RealImportsVerdict.NO_REFERENCE,
        None,
        (),
        "subject neither imported nor patched in this test",
    )


def compute_ci_parity(
    source: str,
    target: str | None,
    *,
    env_parity: bool = True,
) -> CIParityResult:
    """Build the CI-parity signal for one test.

    Args:
        source: The literal text of the generated test file.
        target: The planner's subtask ``target`` pointer.
        env_parity: Whether the test is graded under the CI-parity runner
            env (creds blanked + UTC + isolation). The runner controls
            this; the unit/api Docker lanes pass it through ``run_pytest``.
    """
    verdict, subject, mocked, reason = check_real_imports(source, target)
    return CIParityResult(
        env_parity=env_parity,
        real_imports=verdict,
        target_module=subject,
        mocked_targets=mocked,
        reason=reason,
    )

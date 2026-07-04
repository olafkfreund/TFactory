"""Mutate-and-check probe — Task 7 (#8) commit 3.

The strongest of the five evaluation signals: catch tautological tests
(``assert True``, ``assert 1 == 1``, ``assert x == x``) and shallow
"happy path" tests whose assertions don't actually constrain the
behaviour under test.

The probe works by applying a SINGLE mutation to one assertion in
the generated test source, then re-running it. If the mutated test
*still passes*, the assertion isn't actually checking what it claims —
the test "survived" the mutation. The Evaluator's verdict logic
treats SURVIVED as a hard reject.

Mutation operators (one per probe; we pick the cheapest applicable):

  ``==`` → ``!=``      (flip equality)
  ``!=`` → ``==``      (flip inequality)
  ``<``  → ``>=``      (flip strict-less)
  ``>``  → ``<=``      (flip strict-greater)
  ``<=`` → ``>``       (flip ≤)
  ``>=`` → ``<``       (flip ≥)
  ``True``  → ``False``
  ``False`` → ``True``
  numeric literal → ``literal + 1`` (1 → 2, 0 → 1, -3 → -2)

The probe applies ONE mutation per candidate (the first applicable one
found by AST walk) — same logic mutation testing tools like mutmut and
cosmic-ray use, but only one mutant per candidate (cheap, deterministic).

The Evaluator commit-5 wiring will:
  1. For each generated test that passed the Executor's first run
     AND the stability runner's verdict==STABLE check, mutate the
     test source via ``mutate_source()``.
  2. Write the mutated source to a tmp path.
  3. Call the runner_fn with the mutated path.
  4. KILLED (runner exits non-zero) → keep test. SURVIVED (runner
     exits 0) → reject. ERROR / no mutation applicable → inconclusive.

#630: a generated test FILE can hold several ``test_*`` functions for the
same acceptance criterion — e.g. one weak status-only test alongside a
stronger value-asserting one. A single file-wide "first applicable node"
mutation can land inside the weak function and survive there while the
strong function (untouched, and still green) masks that survival at the
whole-file exit code. ``run_mutate_probe`` now builds ONE mutation
candidate per ``test_*`` function (``mutate_source_candidates``) and takes
the strongest signal across all of them — KILLED if ANY function's
mutation is caught, so a real value-asserting sibling is never masked by a
weaker one. A file with a single test function reduces to exactly the
original single-mutation behaviour.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agents.run_result import RunResultLike


class MutationVerdict(str, Enum):
    """Outcome of running a mutated test."""

    KILLED = "killed"  # mutant failed — assertion is meaningful
    SURVIVED = "survived"  # mutant passed — assertion is tautological
    NO_MUTATION = "no_mutation"  # nothing to mutate (no assertions found)
    ERROR = "error"  # runner raised, or mutation broke parse


# Shared structural result contract (extracted to agents/run_result.py, #426).
# Aliased to the historical local name so the annotation below stays unchanged.
_RunResultLike = RunResultLike


@dataclass(frozen=True)
class MutationApplied:
    """Record of the single mutation the probe applied.

    Useful for the verdicts.json — the Evaluator can quote the exact
    mutation to the Triager / reviewer so they can sanity-check.
    """

    operator: str  # e.g., "Eq->NotEq", "Constant:1->2"
    lineno: int
    before: str  # the original AST node's source segment
    after: str  # the mutated AST node's source segment


@dataclass(frozen=True)
class MutationResult:
    """Aggregate verdict + per-mutation record from ``run_mutate_probe``."""

    verdict: MutationVerdict
    mutation: MutationApplied | None = None
    mutated_source: str | None = None
    error_message: str | None = None
    runner_stdout_tail: str = ""
    runner_stderr_tail: str = ""

    @property
    def is_acceptable(self) -> bool:
        """Convenience: did the mutation kill (= assertion is real)?

        NO_MUTATION is treated as acceptable here because a test with
        no assertions at all is caught by other signals (the Executor's
        first run would have exited 0 with pytest's "no tests collected"
        warning, etc.). The Evaluator's verdict assembly can override
        this if desired.
        """
        return self.verdict in (MutationVerdict.KILLED, MutationVerdict.NO_MUTATION)


# ─── Mutator ────────────────────────────────────────────────────────────


class _AssertMutator(ast.NodeTransformer):
    """Mutate the FIRST applicable assertion node in an AST.

    Walks top-down; first applicable mutation wins. Sets
    ``self.mutation`` to a ``MutationApplied`` describing what was
    changed, or leaves it ``None`` if nothing was mutated.

    Restricted to nodes INSIDE the test functions (def test_*) to
    avoid mutating top-level constants the test imports.
    """

    _COMPARE_FLIP = {
        ast.Eq: (ast.NotEq, "Eq->NotEq"),
        ast.NotEq: (ast.Eq, "NotEq->Eq"),
        ast.Lt: (ast.GtE, "Lt->GtE"),
        ast.Gt: (ast.LtE, "Gt->LtE"),
        ast.LtE: (ast.Gt, "LtE->Gt"),
        ast.GtE: (ast.Lt, "GtE->Lt"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.mutation: MutationApplied | None = None
        self._inside_test_fn = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        prev = self._inside_test_fn
        if node.name.startswith("test_"):
            self._inside_test_fn = True
        self.generic_visit(node)
        self._inside_test_fn = prev
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        prev = self._inside_test_fn
        if node.name.startswith("test_"):
            self._inside_test_fn = True
        self.generic_visit(node)
        self._inside_test_fn = prev
        return node

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if self.mutation is not None or not self._inside_test_fn:
            return self.generic_visit(node)
        if not node.ops:
            return self.generic_visit(node)
        op_type = type(node.ops[0])
        if op_type not in self._COMPARE_FLIP:
            return self.generic_visit(node)
        new_cls, op_name = self._COMPARE_FLIP[op_type]
        before = ast.unparse(node)
        node.ops[0] = new_cls()
        after = ast.unparse(node)
        self.mutation = MutationApplied(
            operator=op_name,
            lineno=node.lineno,
            before=before,
            after=after,
        )
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if self.mutation is not None or not self._inside_test_fn:
            return node
        # Only mutate booleans and ints (not strings, not floats, not None).
        if isinstance(node.value, bool):
            new_val = not node.value
            self.mutation = MutationApplied(
                operator=f"Constant:{node.value}->{new_val}",
                lineno=node.lineno,
                before=repr(node.value),
                after=repr(new_val),
            )
            return ast.copy_location(ast.Constant(value=new_val), node)
        if isinstance(node.value, int):
            new_val = node.value + 1
            self.mutation = MutationApplied(
                operator=f"Constant:{node.value}->{new_val}",
                lineno=node.lineno,
                before=repr(node.value),
                after=repr(new_val),
            )
            return ast.copy_location(ast.Constant(value=new_val), node)
        return node


def mutate_source(source: str) -> tuple[str | None, MutationApplied | None]:
    """Apply ONE mutation to the first applicable assertion.

    Returns:
        ``(mutated_source, applied)`` if a mutation was applied;
        ``(None, None)`` if nothing was mutable OR the source has
        a syntax error.

    The mutator prefers Compare ops over Constants. Constants are
    only mutated if no Compare op is found INSIDE a test_* function.
    Outside-test code (imports, fixtures, module-level) is left alone.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, None

    # First pass: prefer Compare mutations.
    mutator = _AssertMutator()
    mutator.visit(tree)

    if mutator.mutation is None:
        return None, None

    try:
        mutated = ast.unparse(tree)
    except Exception:  # noqa: BLE001 — defensive; unparse can fail on weird ASTs
        return None, None

    return mutated, mutator.mutation


def _safe_unparse(tree: ast.AST) -> str | None:
    """``ast.unparse`` that swallows the (rare) unparse failure to ``None``."""
    try:
        return ast.unparse(tree)
    except Exception:  # noqa: BLE001 — defensive; unparse can fail on weird ASTs
        return None


def _test_function_defs(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Module-level ``test_*`` function defs, in source order.

    Only top-level defs — the shape Gen-Functional writes (flat test
    files, no wrapping classes). A file with a single test function
    yields a single-element list.
    """
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]


def mutate_source_candidates(source: str) -> list[tuple[str, MutationApplied]]:
    """One mutation candidate per ``test_*`` function, in source order (#630).

    Each candidate mutates ONLY the first applicable node found INSIDE
    that one function (same Compare-preferred-over-Constant rule as
    ``mutate_source``), leaving every sibling test function byte-for-byte
    untouched. This is what lets ``run_mutate_probe`` give a strong
    value-asserting test its own shot at catching a mutant, instead of
    the file's single mutation landing arbitrarily in whichever function
    happens to contain the first mutable node.

    Returns ``[]`` on a syntax error or when no function has anything
    mutable — same "give up cleanly" contract as ``mutate_source``.
    """
    try:
        base_tree = ast.parse(source)
    except SyntaxError:
        return []

    names = [fn.name for fn in _test_function_defs(base_tree)]
    candidates: list[tuple[str, MutationApplied]] = []
    for name in names:
        tree = ast.parse(source)  # fresh tree so mutations don't accumulate
        target = next(fn for fn in _test_function_defs(tree) if fn.name == name)
        mutator = _AssertMutator()
        mutator.visit(target)
        if mutator.mutation is None:
            continue
        mutated = _safe_unparse(tree)
        if mutated is None:
            continue
        candidates.append((mutated, mutator.mutation))
    return candidates


# ─── Public entrypoint ──────────────────────────────────────────────────


def _probe_one_candidate(
    mutated: str,
    mutation: MutationApplied,
    test_file: Path,
    project_dir: Path,
    runner_fn: Callable[[Path, Path, int], _RunResultLike],
    *,
    write_mutant_to: Path | None,
    seed: int,
    tail_chars: int,
) -> MutationResult:
    """Run one mutation candidate through the runner; ERROR/KILLED/SURVIVED."""
    # Caller-controlled placement of the mutant file.
    runner_target = test_file
    if write_mutant_to is not None:
        try:
            write_mutant_to.parent.mkdir(parents=True, exist_ok=True)
            write_mutant_to.write_text(mutated)
            runner_target = write_mutant_to
        except OSError as exc:
            return MutationResult(
                verdict=MutationVerdict.ERROR,
                mutation=mutation,
                mutated_source=mutated,
                error_message=f"could not write mutant to {write_mutant_to}: {exc}",
            )

    try:
        res = runner_fn(runner_target, project_dir, seed)
    except Exception as exc:  # noqa: BLE001 — runner errors → ERROR verdict
        return MutationResult(
            verdict=MutationVerdict.ERROR,
            mutation=mutation,
            mutated_source=mutated,
            error_message=f"{type(exc).__name__}: {exc}"[:500],
        )

    verdict = (
        MutationVerdict.KILLED if res.returncode != 0 else MutationVerdict.SURVIVED
    )
    return MutationResult(
        verdict=verdict,
        mutation=mutation,
        mutated_source=mutated,
        runner_stdout_tail=(res.stdout or "")[-tail_chars:],
        runner_stderr_tail=(res.stderr or "")[-tail_chars:],
    )


def run_mutate_probe(
    test_file: Path,
    project_dir: Path,
    runner_fn: Callable[[Path, Path, int], _RunResultLike],
    *,
    write_mutant_to: Path | None = None,
    seed: int = 0,
    tail_chars: int = 500,
) -> MutationResult:
    """Run the mutate-and-check probe against one test file.

    Builds one mutation candidate per ``test_*`` function in the file
    (``mutate_source_candidates``) and runs each in turn. The first
    KILLED result wins outright — some test in the file demonstrably
    catches that class of mutation, so the file (and the acceptance
    criterion it verifies) is not falsely penalised for a sibling
    function's weak assertions (#630). If nothing is killed, the result
    is exactly what the original single-mutation probe would have
    returned (the first function's candidate) — a genuinely weak-only
    file still SURVIVES.

    Args:
        test_file: Absolute path to the original generated test file.
        project_dir: Project root passed through to ``runner_fn``.
        runner_fn: Same shape as ``stability_runner.check_stability``'s
            seam: ``runner_fn(test_file, project_dir, seed) -> RunResultLike``.
            The Evaluator commit-5 wiring will write the mutated source
            to a tmp path before passing it as ``test_file``.
        write_mutant_to: If provided, the mutated source is written
            here AND the runner is called with this path instead of the
            original. If ``None``, the runner is called with the
            original path (the mutated source is returned in the
            result for the caller to write).
        seed: Forwarded to ``runner_fn``.
        tail_chars: How many trailing chars of stdout/stderr to keep
            for the verdicts.json.

    Returns:
        MutationResult capturing the verdict + the mutation applied.
    """
    if not test_file.exists():
        return MutationResult(
            verdict=MutationVerdict.ERROR,
            error_message=f"test file not found: {test_file}",
        )

    try:
        source = test_file.read_text()
    except OSError as exc:
        return MutationResult(
            verdict=MutationVerdict.ERROR,
            error_message=f"could not read {test_file}: {exc}",
        )

    candidates = mutate_source_candidates(source)
    if not candidates:
        return MutationResult(verdict=MutationVerdict.NO_MUTATION)

    first_result: MutationResult | None = None
    for mutated, mutation in candidates:
        candidate_result = _probe_one_candidate(
            mutated,
            mutation,
            test_file,
            project_dir,
            runner_fn,
            write_mutant_to=write_mutant_to,
            seed=seed,
            tail_chars=tail_chars,
        )
        if candidate_result.verdict == MutationVerdict.KILLED:
            # Strongest possible signal: some test in this file DOES catch
            # this class of mutation (#630) — no need to probe the rest.
            return candidate_result
        if first_result is None:
            first_result = candidate_result

    # Nothing was killed — report the first function's candidate, exactly
    # what the pre-#630 single-mutation probe would have returned.
    if first_result is None:  # pragma: no cover — unreachable, candidates non-empty
        raise RuntimeError("run_mutate_probe: no candidate produced a result")
    return first_result

"""Pre-flight static check for Gen-Functional — Task 6 (#7) commit 2.

Before Gen-Functional commits an LLM-generated test file, this module
verifies that every import + every from-import attribute actually
resolves against the target project's Python environment. Catches the
single biggest failure mode in LLM-generated tests: hallucinated
imports + nonexistent methods (cited in the design plan's research as
~39% of Python test failures).

The check is a two-step pass:

  1. AST extraction — walk the generated source; collect every Import
     / ImportFrom node with line numbers. Aliased + multi-name imports
     expand into one entry per actual name.

  2. Subprocess introspection — for each extracted import, spawn
     `python -c "..."` with the target project's directory on
     PYTHONPATH and try to import the module (and, for from-imports,
     getattr the name). An importable module with a missing attribute is
     a hallucination (fail); a module that's simply ABSENT from this
     interpreter is an environment gap, not a hallucination — it's skipped
     (the generation venv is not the test-execution env). See #707.

Failures don't raise — they collect into a PreflightResult that the
caller (Gen-Functional in commit 5) uses to decide between (a) write
the test file, (b) write context/replan_request.json for the Planner.

What this DOES NOT check yet (covered in commit 3 via the flake-lint
sibling, or deferred):

  - Method calls like `auth.bcrypt_hash(...)` where `auth` is a local
    variable — needs symbol tracking, not just import tables.
  - Type-only imports (`if TYPE_CHECKING: import ...`) — treated as
    regular imports; the subprocess check will report them as failed
    if they're conditional-only modules.
  - Imports inside try/except blocks meant to be soft — same caveat.

For MVP, "every textual import statement must resolve" is the bar.
LLM-emitted hallucinations almost always show up at this layer.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Result + per-import dataclasses ─────────────────────────────────────


@dataclass
class PreflightImport:
    """One import statement (or one name in a from-import) we'll verify.

    For ``import a.b.c``: module='a.b.c', name=None
    For ``from a.b import c``: module='a.b', name='c'
    For ``from a.b import c, d as e``: two entries, names 'c' and 'd'
        (alias 'e' tracked separately; we verify the ORIGINAL name)
    For ``from a.b import *``: module='a.b', name='*' — module-only check
    For ``from . import x``: module='.x', name=None, is_relative=True
        — skipped at check time with a "couldn't verify" note
    """

    module: str
    name: str | None = None
    alias: str | None = None
    lineno: int = 0
    is_relative: bool = False

    # Filled in by check_import; defaults represent "not yet checked".
    failed: bool = False
    reason: str | None = None
    skipped: bool = False  # relative imports + similar non-checkable cases

    def describe(self) -> str:
        if self.name is None:
            return f"import {self.module}"
        if self.name == "*":
            return f"from {self.module} import *"
        a = f" as {self.alias}" if self.alias else ""
        return f"from {self.module} import {self.name}{a}"


@dataclass
class PreflightResult:
    """Outcome of one pre-flight check over a generated test source."""

    ok: bool
    imports_checked: list[PreflightImport] = field(default_factory=list)
    syntax_error: str | None = None  # set when the test source itself doesn't parse

    @property
    def failures(self) -> list[PreflightImport]:
        return [i for i in self.imports_checked if i.failed]

    @property
    def skipped(self) -> list[PreflightImport]:
        return [i for i in self.imports_checked if i.skipped]

    def summary(self) -> str:
        if self.syntax_error:
            return f"syntax error: {self.syntax_error}"
        if self.ok:
            return f"OK ({len(self.imports_checked)} imports checked)"
        bits = [f"{len(self.failures)} failed"]
        if self.skipped:
            bits.append(f"{len(self.skipped)} skipped")
        return ", ".join(bits)


# ─── AST extraction ──────────────────────────────────────────────────────


def extract_imports(source: str) -> tuple[list[PreflightImport], str | None]:
    """Walk the AST and collect every import statement.

    Returns ``(imports, syntax_error)``. On a parse failure, imports is
    empty and syntax_error has the exception text.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})"

    out: list[PreflightImport] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import a, b.c, d as e` → three entries
            for alias in node.names:
                out.append(
                    PreflightImport(
                        module=alias.name,
                        name=None,
                        alias=alias.asname,
                        lineno=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_relative = node.level > 0
            # Reconstruct relative dotting for human-readable description
            if is_relative:
                module = "." * node.level + module
            for alias in node.names:
                out.append(
                    PreflightImport(
                        module=module,
                        name=alias.name,  # may be '*'
                        alias=alias.asname,
                        lineno=node.lineno,
                        is_relative=is_relative,
                    )
                )

    return out, None


# ─── Subprocess introspection ───────────────────────────────────────────


# Introspection subprocess exit codes (kept in sync with the scripts below).
_EXIT_OK = 0
_EXIT_IMPORT_ERROR = 1  # module raised a non-ModuleNotFound error at import
_EXIT_MISSING_ATTR = 2  # module imports but the from-name is absent (hallucination)
_EXIT_MODULE_ABSENT = 3  # ModuleNotFoundError — environment gap, not a hallucination

# NOTE on the script templates: we use plain str.format() substitution
# of repr'd values, then plain string concatenation (NOT f-strings) for
# error reporting. Nested f-strings + injected repr'd strings collide on
# quote characters and produce SyntaxError at the subprocess level.
# Exit codes: 0 = ok, 1 = import raised (real error), 2 = module imports but
# the from-name is neither an attribute NOR an importable submodule (genuine
# hallucination), 3 = environment gap — the module (or a from-name's submodule
# dep) is simply absent from THIS interpreter, not a hallucination; see
# check_import for why we skip rather than fail.
_INTROSPECT_SCRIPT_IMPORT = """\
import importlib, sys
_mod = {module!r}
try:
    importlib.import_module(_mod)
except ModuleNotFoundError as e:
    sys.stderr.write('ModuleNotFoundError: ' + str(e))
    sys.exit(3)
except Exception as e:
    sys.stderr.write(type(e).__name__ + ': ' + str(e))
    sys.exit(1)
"""

_INTROSPECT_SCRIPT_FROM = """\
import importlib, sys
_mod = {module!r}
_name = {name!r}
try:
    m = importlib.import_module(_mod)
except ModuleNotFoundError as e:
    sys.stderr.write('import ' + _mod + ': ModuleNotFoundError: ' + str(e))
    sys.exit(3)
except Exception as e:
    sys.stderr.write('import ' + _mod + ': ' + type(e).__name__ + ': ' + str(e))
    sys.exit(1)
if hasattr(m, _name):
    sys.exit(0)
# _name is not an attribute of the parent package, but it may still be a
# SUBMODULE that importing the package did not bind: importlib.import_module(pkg)
# runs only pkg/__init__.py and does NOT import submodules, so `from pkg import
# sub` leaves `sub` absent as an attribute here even though the real test run
# resolves it fine. Probe the submodule before declaring a hallucination (#712;
# same env-vs-hallucination distinction as the top-level ModuleNotFoundError,
# #707/#709).
_target = _mod + '.' + _name
try:
    importlib.import_module(_target)
    sys.exit(0)
except ModuleNotFoundError as e:
    if getattr(e, 'name', None) == _target:
        # Neither an attribute nor an importable submodule: the name is
        # genuinely absent -> hallucination. Preserve the guard's real value.
        # Report WHICH file answered: three separate defects (#732 wrong roots,
        # #742 wrong branch, #752 CWD outranking both) all surfaced as this one
        # indistinguishable message, and each cost hours to tell apart.
        _f = getattr(m, '__file__', None) or '?'
        sys.stderr.write(_mod + ' has no attribute ' + _name + ' [resolved: ' + str(_f) + ']')
        sys.exit(2)
    # The submodule exists but one of ITS deps is absent from the generation
    # env. Unverifiable here; the test-execution env resolves it. Skip.
    sys.stderr.write('submodule ' + _target + ' present; dep absent: ' + str(e))
    sys.exit(3)
except Exception as e:
    # Submodule imports but errors in the generation env -> unverifiable here.
    sys.stderr.write('submodule ' + _target + ': ' + type(e).__name__ + ': ' + str(e))
    sys.exit(3)
"""


# Directory names that never contain the project's own importable packages —
# skipped when locating package roots so the scan stays cheap and cannot pick
# a vendored copy over the checkout's own.
_ROOT_SCAN_SKIP = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "build",
        "dist",
        ".aifactory",
        ".tfactory",
    }
)


def package_roots_for(project_dir: Path, module: str) -> list[str]:
    """Dirs to prepend to PYTHONPATH so ``module`` resolves from THIS checkout.

    ``<project>`` and ``<project>/src`` cover flat and src-layout repos, but a
    monorepo keeps its packages a level or two down -- ``apps/web-server/server``
    in the Factory repos. Without the real root, ``import server.routes.git``
    either fails (harmless: exit 1 is skipped) or, far worse, resolves against
    whatever copy of that package is already on the ambient PYTHONPATH -- the
    running service's own -- and reports a brand-new function as a hallucinated
    attribute. That is not hypothetical: it burned an entire replan budget and
    failed a verify run against correct code (#732).

    Returns the directories CONTAINING ``module``'s top-level package, nearest
    first, so the checkout always wins over anything inherited.
    """
    top = module.split(".", 1)[0]
    if not top:
        return []
    roots: list[str] = []
    # Bounded: package at <project>/<pkg>, one level down, or two (apps/<app>/<pkg>).
    for pattern in (f"{top}", f"*/{top}", f"*/*/{top}"):
        for hit in sorted(project_dir.glob(pattern)):
            if not (hit / "__init__.py").is_file():
                continue
            if any(
                part in _ROOT_SCAN_SKIP for part in hit.relative_to(project_dir).parts
            ):
                continue
            parent = str(hit.parent)
            if parent not in roots:
                roots.append(parent)
    return roots


def package_root_rel_paths(root: Path) -> list[str]:
    """Repo-relative dirs that must be importable for this tree's packages.

    The execution-time counterpart of ``package_roots_for``. That one answers
    "where does THIS module live" for the generation probe; this one answers
    "what does pytest need on PYTHONPATH to collect anything here", because at
    execution time there is no single module to ask about — the runner is handed
    a directory of tests.

    Why it exists: the runners hardcoded ``<root>/src`` and ``<root>``, which
    covers flat and src-layout repos and misses a monorepo entirely. The package
    under test in the Factory repos is ``apps/web-server/server``, reachable from
    neither, so collection failed before a single assertion ran and every
    acceptance criterion came back as an import error against correct code
    (#756). #732 fixed this for the probe; the runners kept the old behaviour.

    Returns the PARENT of each top-level package, nearest first, relative to
    ``root`` (``"."`` for a package sitting at the top). Nested packages are
    skipped — only the outermost one needs to be on the path, and adding inner
    ones invites shadowing.
    """
    rels: list[str] = []
    # Bounded the same way as package_roots_for: top level, or one/two down.
    for pattern in ("*/__init__.py", "*/*/__init__.py", "*/*/*/__init__.py"):
        for hit in sorted(root.glob(pattern)):
            pkg = hit.parent
            rel = pkg.relative_to(root)
            if any(part in _ROOT_SCAN_SKIP for part in rel.parts):
                continue
            if (pkg.parent / "__init__.py").is_file():
                continue  # inner package; its top-level parent already counts
            parent = str(rel.parent)
            if parent not in rels:
                rels.append(parent)
    return rels


def requirements_files(root: Path, *, limit: int = 5) -> list[Path]:
    """``requirements.txt`` files in this checkout, nearest first.

    The host runner only ever looked at ``<project>/requirements.txt``, which a
    monorepo does not have: this repo declares its dependencies in
    ``apps/web-server/requirements.txt`` and ``apps/backend/requirements.txt``,
    and the root has neither that file nor a ``pyproject.toml``. Both install
    branches were therefore skipped, the lane venv got pytest and nothing else,
    and every test that imported the app died at collection on a missing
    third-party package — reported as an error against correct code (#759).

    Bounded and vendor-skipped like ``package_root_rel_paths``; ``limit`` guards
    against a repo that vendors hundreds of them.
    """
    found: list[Path] = []
    for pattern in ("requirements.txt", "*/requirements.txt", "*/*/requirements.txt"):
        for hit in sorted(root.glob(pattern)):
            rel = hit.relative_to(root)
            if any(part in _ROOT_SCAN_SKIP for part in rel.parts):
                continue
            if hit not in found:
                found.append(hit)
            if len(found) >= limit:
                return found
    return found


def _shadowed_elsewhere(
    detail: str, module: str, project_dir: Path | None
) -> str | None:
    """The path that answered, when it is NOT the checkout's own copy.

    A missing attribute usually means the test named something that does not
    exist — the guard's whole purpose. But it means something entirely different
    when the import was answered by a DIFFERENT copy of a package the checkout
    also provides: then the symbol may exist perfectly well in the code under
    test and the probe simply read the wrong file. Three defects landed here in
    one day (#732 wrong roots, #742 wrong branch, #752 CWD outranking both), all
    reported identically as a hallucination against correct code.

    Returns the offending path when the checkout DOES provide this package and
    something outside the checkout answered; ``None`` otherwise. The
    checkout-provides test is what keeps third-party and stdlib modules honest:
    ``from json import nope`` resolves outside the checkout too, and that really
    is a hallucination, so it must keep failing.
    """
    if project_dir is None:
        return None
    m = re.search(r"\[resolved: ([^\]]+)\]", detail)
    if not m:
        return None
    resolved = m.group(1).strip()
    if resolved in ("", "?"):
        return None
    if not package_roots_for(project_dir, module):
        return None  # not a package this checkout owns — a real missing name
    try:
        Path(resolved).resolve().relative_to(Path(project_dir).resolve())
    except ValueError:
        return resolved  # answered from outside the checkout: shadowed
    return None


def _classify_nonzero(
    imp: PreflightImport,
    proc: subprocess.CompletedProcess[str],
    project_dir: Path | None,
) -> PreflightImport:
    """Turn a non-zero probe exit into failed / skipped, with the file that answered."""
    detail = (proc.stderr or proc.stdout or "exit non-zero").strip()

    if proc.returncode == _EXIT_MISSING_ATTR:
        shadow = _shadowed_elsewhere(detail, imp.module, project_dir)
        if shadow:
            # Not a hallucination: a different copy of this package answered, so
            # the symbol's absence says nothing about the code under test. Skip
            # rather than reject — the same environment-vs-hallucination call
            # #707 makes for a missing module.
            imp.skipped = True
            imp.reason = (
                f"{imp.module}.{imp.name} not found, but the import resolved to "
                f"{shadow}, outside {project_dir} — the checkout's own copy was "
                "shadowed, so this is a resolution failure, not a hallucination"
            )[:300]
            return imp

    if proc.returncode != 0:
        imp.failed = True
        imp.reason = detail[:300]
    return imp


def check_import(
    imp: PreflightImport,
    *,
    project_dir: Path | None = None,
    python_exe: str | None = None,
    timeout_sec: float = 15.0,
) -> PreflightImport:
    """Mutate ``imp`` with check results. Returns the same object."""
    if imp.is_relative:
        imp.skipped = True
        imp.reason = "relative import — can't verify without package context"
        return imp

    python_exe = python_exe or sys.executable

    if imp.name in (None, "*"):
        script = _INTROSPECT_SCRIPT_IMPORT.format(module=imp.module)
    else:
        script = _INTROSPECT_SCRIPT_FROM.format(module=imp.module, name=imp.name)

    env = os.environ.copy()
    if project_dir is not None:
        existing = env.get("PYTHONPATH", "")
        # Put the project root on PYTHONPATH, and — for src-layout packages
        # (``<project>/src/<pkg>``) — the ``src`` dir too, so ``import <pkg>``
        # resolves without an install step.
        roots = [str(project_dir)]
        src_dir = Path(project_dir) / "src"
        if src_dir.is_dir():
            roots.insert(0, str(src_dir))
        # #732: put the dir that actually CONTAINS this module's package first,
        # so a monorepo layout resolves from the checkout instead of falling
        # through to an ambient copy of the same package name.
        for found in reversed(package_roots_for(Path(project_dir), imp.module)):
            if found in roots:
                roots.remove(found)
            roots.insert(0, found)
        parts = roots + ([existing] if existing else [])
        env["PYTHONPATH"] = os.pathsep.join(parts)

    try:
        proc = subprocess.run(
            [python_exe, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            # Run from the checkout, never from wherever TFactory happens to be
            # running. `python -c` puts the CWD at the front of sys.path, ahead
            # of everything PYTHONPATH says, so inheriting the service's own
            # working directory silently outranked the roots computed above.
            #
            # That is invisible until TFactory verifies a repo whose package
            # names collide with its own — verifying TFactory itself, where both
            # trees provide `server.routes.git`. The probe then imported the
            # RUNNING SERVICE's copy, found the build's new symbol absent, and
            # reported a correct test as a hallucination (#752). #732 fixed which
            # roots go on PYTHONPATH; this fixes CWD outranking them.
            cwd=str(project_dir) if project_dir is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired:
        imp.failed = True
        imp.reason = f"introspection timed out after {timeout_sec}s"
        return imp
    except FileNotFoundError as exc:
        imp.failed = True
        imp.reason = f"python exe not found: {exc}"
        return imp

    if proc.returncode == _EXIT_MODULE_ABSENT:
        # ModuleNotFoundError: the module isn't present in the GENERATION
        # interpreter (sys.executable = TFactory's service venv), which is NOT
        # the test-execution environment (nix/docker) where the test actually
        # runs. Third-party libs the test imports (requests/httpx) and the SUT's
        # own transitive deps (fastapi, ...) live there, not here. Treating an
        # absent module as a hallucination false-rejects a correct test, which
        # replan-loops the subtask to `stuck` -> generated_empty and verify
        # never runs (#707, same family as #609/#613). Skip: the real test run
        # resolves it. Genuine hallucinations (importable module, missing
        # attribute) still fail via exit code 2 below.
        imp.skipped = True
        imp.reason = (
            proc.stderr or proc.stdout or "module not present in generation env"
        ).strip()[:300]
        return imp

    return _classify_nonzero(imp, proc, project_dir)


# ─── Top-level entry point ──────────────────────────────────────────────


def preflight_check(
    test_source: str,
    *,
    project_dir: Path | None = None,
    python_exe: str | None = None,
    timeout_per_import_sec: float = 15.0,
    skip_stdlib_check: bool = False,
) -> PreflightResult:
    """Run the full pre-flight check on one generated test source.

    Args:
        test_source: The Python source the LLM produced.
        project_dir: Root of the target project (added to PYTHONPATH).
            Pass None to check against TFactory's own environment only
            (mostly useful in unit tests where stdlib resolution
            suffices).
        python_exe: Override the Python interpreter. Defaults to
            ``sys.executable``. For the eventual per-project venv use
            case, callers pass the target project's venv python.
        timeout_per_import_sec: Per-import subprocess cap.
        skip_stdlib_check: If True, common stdlib modules
            (json/pathlib/etc.) are skipped without subprocess. Faster
            but slightly less rigorous.

    Returns:
        PreflightResult with one entry per import + summary stats.
    """
    imports, syntax_err = extract_imports(test_source)
    if syntax_err is not None:
        return PreflightResult(ok=False, imports_checked=[], syntax_error=syntax_err)

    stdlib_names = set(sys.stdlib_module_names) if skip_stdlib_check else set()

    for imp in imports:
        if skip_stdlib_check and imp.module.split(".")[0] in stdlib_names:
            imp.skipped = True
            imp.reason = "stdlib — skipped"
            continue
        check_import(
            imp,
            project_dir=project_dir,
            python_exe=python_exe,
            timeout_sec=timeout_per_import_sec,
        )

    ok = all(not i.failed for i in imports)
    return PreflightResult(ok=ok, imports_checked=imports)

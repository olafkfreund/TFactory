#!/usr/bin/env python3
"""Diff-scoped lint ratchet for the TFactory Python backend.

Originally vendored from the Factory fleet reference (CFactory's
scripts/ratchet_lint.py) - intentional cross-service reuse so every service runs
the identical no-regression ratchet. The package default and shared-config paths
are adjusted for this repo's layout, and the blocking per-file mypy gate (#449)
mirrors PFactory's implementation (PFactory #192).

Implements the Factory coding-standards ratchet (coding-standards.md sections 0
and 4.6): the strict bar (`ruff` with the shared select set + `mypy --strict`)
is enforced on the files a PR changes, and a changed file MAY NOT REGRESS - i.e.
it may not gain ruff OR mypy violations relative to the PR base. Untouched legacy
hotspots are allowed until touched, and the existing legacy backlog inside a
touched file does not block (a whole-repo strict gate would be instantly red at
adoption). New code and any net-new violation a PR introduces are blocked.

Mechanism (ruff): for each changed Python file, count ruff violations (shared
config) at the PR base and at HEAD; fail if HEAD has more. `ruff format`
reflowing legacy lines never increases the count, so a pure-cleanup PR stays
green while genuine new violations are caught.

Mechanism (mypy): same no-regression model. For each changed Python file, run
`mypy --strict` (standards/mypy.ini) and count the errors mypy attributes to
that file, at the PR base and at HEAD; fail if HEAD has more. mypy is invoked
from inside the package dir (`apps/backend`) with the file path relative to it
and `--explicit-package-bases --namespace-packages`, so first-party imports
resolve as they do at runtime (PYTHONPATH=apps/backend) instead of being
double-named via the stray `apps/backend/__init__.py`. The target Python version
comes from the interpreter the gate runs under, not from the shared baseline's
floor of 3.11 (issue #968 - see `interpreter_target`). The base count is taken
by swapping the file's content to its base version in place (HEAD content is
restored afterwards, always). Errors mypy reports in OTHER files (imported
modules) are not attributed to the changed file and so never gate it.

Usage:
    python scripts/ratchet_lint.py --base <git-ref> [--package <dir>] \\
        [--mypy-config <ini>] [--no-mypy]

Exit code 0 if no changed file regressed; 1 otherwise.
"""

# T201 (no print) targets SERVICE code, where stdout is not an output channel.
# This is a CI command-line tool whose entire product is what it prints: the
# gated file list, the per-rule regression lines and the verdict all go to the
# CI log, and the workflow has no other way to show them. Scoped to the one
# rule: the code-less blanket form is what PGH004 forbids, and it would hide the
# next real finding in this file. Same carve-out PFactory's fork already carries.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from types import MappingProxyType

# Canonical shared ratchet rules, vendored byte-exact from the Factory hub
# and byte-exact drift-gated (Factory#403). scripts/ is sys.path[0] when this
# runs as a script, so the sibling import resolves without packaging.
# write_temp is deliberately NOT imported: mypy runs on the file in place here
# (see mypy_errors) and ruff is fed stdin, so nothing in this fork needs a temp
# copy any more.
from ratchet_helpers import (
    MYPY_TEST_RELAX,
    is_test_file,
    require_tool_ran,
    ruff_findings,
    ruff_stdin_argv,
)

# Strict shared baseline vendored from the Factory hub (standards/.hub-sha).
RUFF_CONFIG = "standards/ruff.toml"
MYPY_CONFIG_DEFAULT = "standards/mypy.ini"
PACKAGE_DEFAULT = "apps/backend"
# mypy emits "<path>:<line>: error: <msg>  [code]"; count only real errors.
_MYPY_ERROR_RE = re.compile(r"^(?P<path>.+?):\d+: error:")

# Byte-exact vendored copies of Factory-hub canonical modules. These are NOT
# governed by this repo's strict style bar — they MUST stay identical to the hub
# canonical and are policed by the byte-exact drift gates
# (verification-core-drift.yml) instead. Reformatting/retyping them to satisfy
# the local ratchet would make them diverge from the hub and break that gate, so
# the ratchet skips them (the same reason ruff.toml excludes them from
# format/lint). Paths are relative to the repo root.
VENDORED_SKIP = frozenset(
    {
        # In scope since Factory#597 widened the ratchet to scripts/, and the one
        # file in that directory that must NOT be held to the local bar. root
        # ruff.toml already excludes it from the format gate; this ratchet reads
        # standards/ruff.toml directly, so it needs the exclusion stated here too.
        "scripts/ratchet_helpers.py",
        "apps/backend/agents/verification_gate.py",
        "apps/backend/tools/runners/nix_provisioner.py",
        "apps/backend/tools/runners/artifact_store.py",
    }
)

# Whole vendored trees, skipped for the same reason. Kept separate from
# VENDORED_SKIP because these are prefixes, not exact paths: the factory-github
# provider layer is a mirror of the hub's shared/factory-github/ and is policed
# by factory-github-drift.yml, so its file list is the hub's to change, not ours.
VENDORED_SKIP_DIRS = ("apps/backend/runners/github/",)


def is_vendored(path: str) -> bool:
    """True for byte-exact vendored copies the drift gates own, not the ratchet."""
    return path in VENDORED_SKIP or path.startswith(VENDORED_SKIP_DIRS)


def _run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    # S603: every argv reaching here is assembled in this file from repo-relative
    # config paths and `git`/`ruff` literals — no shell, and no caller-supplied
    # string ever becomes a command word. This is a CI developer tool, not a
    # request-handling surface.
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, check=False, input=stdin
    )


def owning_package(path: str, packages: list[str]) -> str:
    """Which of *packages* the file lives under.

    Needed because ``package`` is used as an IMPORT ROOT for mypy
    (MYPYPATH/PYTHONPATH), not merely as a filter. A file under
    ``apps/web-server`` type-checked with ``apps/backend`` on the path resolves
    its first-party imports against the wrong tree and reports errors that say
    more about the root than the code — so each file must be checked against the
    package it actually belongs to (Factory#384).

    The LONGEST match wins, so a nested package beats its parent.
    """
    target = Path(path)
    matches = [
        pkg
        for pkg in packages
        if Path(pkg) in target.parents or Path(pkg) == target.parent
    ]
    return max(matches, key=len) if matches else packages[0]


def changed_python_files(base: str, packages: list[str]) -> list[str]:
    """Python files under any of *packages* changed vs *base*.

    ``ACMR`` — added, copied, modified, RENAMED. ``diff.renames`` has defaulted
    to true since git 2.9, so a moved file has status R; the previous ``AM``
    excluded it and a rename was gated by nothing at all (#1005). See
    :func:`rename_sources` for why the baseline must follow the move.
    """
    res = _run(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"])
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(2)
    pkgs = [Path(p) for p in packages]
    out: list[str] = []
    for line in res.stdout.splitlines():
        path = Path(line)
        # Match files inside the package dir, or the package dir itself when it
        # is a flat directory (pkg in path.parents covers nested layouts).
        if (
            path.suffix == ".py"
            and any(pkg in path.parents or pkg == path.parent for pkg in pkgs)
            and path.exists()
            and not is_vendored(str(path))  # byte-exact vendored copies
        ):
            out.append(str(path))
    return out


def ruff_counts(source: str, filename: str) -> Counter[str]:
    """Per-rule ruff violation counts for *source* checked as *filename*.

    Fed on stdin under the file's REAL path so ruff's per-file-ignores see the
    same path ``ruff check`` would (Factory#510). The temp copy this used to
    write could not: ruff relativises a path against the project root before
    matching the globs, and a path OUTSIDE that root falls back to the BASENAME
    only. ``**/test_*.py`` and ``**/*_test.py`` therefore matched a copy but
    ``**/tests/**`` never could, so a helper under ``tests/`` named neither way
    was held to the production assert bar the real tree exempts it from.
    """
    res = _run(ruff_stdin_argv(RUFF_CONFIG, filename), stdin=source)
    # The shared "is this run a measurement" rule, both halves (Factory#590 for
    # the exit code, Factory#648 for the output). This used to be an exit-code
    # check plus a `return Counter()` for empty stdout plus a bare
    # `except json.JSONDecodeError`, restated here and in the four sibling
    # ratchets. The empty-stdout branch was the one with teeth: the pinned ruff
    # prints `[]` for a clean run -- including for empty stdin -- so empty
    # stdout was always ruff writing no report, counted as zero violations.
    # Both verdicts now live in the drift-gated canonical.
    return ruff_findings(res)


@cache
def rename_sources(base: str) -> Mapping[str, str]:
    """``(head_path, base_path)`` pairs for files this diff MOVED.

    The other half of the ``ACMR`` change above (#1005). Once renames are
    visible, looking a moved file up on base by its HEAD path finds nothing and
    reads the baseline as **0**, so every pre-existing violation in it reports
    as net-new. AIFactory's fork had exactly that and made a pure ``git mv`` of
    a legacy file report ``0 -> 167`` — a gate punishing the cleanup it exists
    to encourage (AIFactory#1218).

    Renames are what ``-M`` reports; ask git rather than guessing from content
    similarity here. Cached per base — one subprocess, not one per file.

    Returns a read-only ``MappingProxyType``: the value is CACHED and therefore
    shared, so a plain dict could be mutated by one caller and silently observed
    by the next — while a tuple of pairs would make every caller rebuild a dict
    per file, for both the ruff and the mypy leg. The proxy is immutable AND
    directly subscriptable.
    """
    res = _run(
        ["git", "diff", "--name-status", "-M", "--diff-filter=R", f"{base}...HEAD"]
    )
    if res.returncode != 0:
        # Fall back to identity mapping rather than failing — worst case is the
        # pre-#1005 behaviour for moved files — but SAY SO. A gate that quietly
        # gets less accurate is the failure mode this whole change is about: the
        # baseline would silently read as empty for a moved file and nothing
        # would indicate why.
        sys.stderr.write(
            "ratchet: could not read rename information; moved files will be "
            "measured against an empty baseline\n"
        )
        sys.stderr.write(res.stderr)
        return MappingProxyType({})
    pairs: dict[str, str] = {}
    for line in res.stdout.splitlines():
        # `R<similarity>\told\tnew`
        status, _, paths = line.partition("\t")
        old, _, new = paths.partition("\t")
        if status.startswith("R") and old and new:
            pairs[new] = old
    return MappingProxyType(pairs)


def file_at_base(base: str, path: str) -> str | None:
    """The file's content on *base*, following a rename to its old path.

    Identity (the ``path`` the counter judges by) deliberately stays the HEAD
    path in :func:`regressions` — only the CONTENT comes from the old location.
    Judging the two sides under different per-file-ignores is Factory#510.
    """
    src = rename_sources(base).get(path, path)
    res = _run(["git", "show", f"{base}:{src}"])
    return res.stdout if res.returncode == 0 else None


def regressions(base: str, path: str) -> list[str]:
    head_src = Path(path).read_text()
    head_counts = ruff_counts(head_src, path)
    base_src = file_at_base(base, path)
    base_counts = ruff_counts(base_src, path) if base_src is not None else Counter()
    out: list[str] = []
    for code, head_n in head_counts.items():
        base_n = base_counts.get(code, 0)
        if head_n > base_n:
            out.append(
                f"{path}: {code} +{head_n - base_n} (base {base_n} -> head {head_n})"
            )
    return out


def interpreter_target() -> str:
    """The ``--python-version`` this gate must target: the venv it checks against.

    The shared ``standards/mypy.ini`` declares ``python_version = 3.11``. That is
    correct for the hub baseline - it is the fleet FLOOR (coding-standards.md
    section 1, "Python (3.11+)"), the hub's own ratchet still builds 3.11, and
    raising it centrally would raise the floor for every repo and stop mypy
    flagging 3.12-only syntax in the ones that still execute on 3.11. It is wrong
    as THIS gate's target: the venv whose site-packages mypy reads is 3.12
    (ratchet.yml, ci.yml), ``graphiti-core`` pulls numpy in, and numpy's stubs
    there use PEP 695 ``type`` statements. Told to target 3.11 mypy refuses to
    parse them, exits 2 having checked nothing, and every file that reaches numpy
    transitively is ungated - hard-failed by ``require_tool_ran`` if it reported
    nothing, or silently under-counted if it happened to emit one unrelated error
    of its own, which satisfies that guard's ``measured`` arm (issue #968).

    Derived from the running interpreter rather than written as ``3.12``, because
    a literal is exactly how ``3.11`` went stale: the venv moves and the target
    does not. ``mypy`` comes from that same venv (the workflow puts it first on
    PATH and runs this script with its python), so its version is this process's.

    Not a loosening under the tighten-only rule: every strict flag in the shared
    baseline still applies, unchanged. Only the syntax/stdlib level moves, and it
    moves to the one actually in use - which is what mypy would default to on its
    own were the baseline not naming a version.

    Ported from PFactory#472 (PFactory#467), which found the same defect first.
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def mypy_errors(path: str, package: str, mypy_config: str) -> int:
    """Number of mypy --strict errors attributed to *path*.

    Runs mypy from inside *package* so first-party imports resolve as they do at
    runtime (the app puts ``apps/backend`` on ``sys.path``). The stray
    ``apps/backend/__init__.py`` would otherwise make mypy resolve every module
    under two names ("agents" and "backend.agents"), which aborts the whole run;
    ``--explicit-package-bases --namespace-packages`` with MYPYPATH pinned to the
    package dir keeps the single, runtime-faithful name. Only error lines whose
    location is *path* itself are counted (errors surfaced in imported modules
    belong to those files, not the changed one).
    """
    pkg = Path(package).resolve()
    rel = os.path.relpath(Path(path).resolve(), pkg)
    env = dict(os.environ)
    # The package dir is the import base, mirroring the app's runtime sys.path.
    # Sibling app packages are appended because the web server imports the
    # backend at runtime (`sys.path.insert` of apps/backend in the route
    # modules); without them mypy cannot resolve `agents.*` from a web-server
    # file and reports import-not-found, which is unfixable from the file
    # itself and blocks any NEW file, whose base count is 0.
    siblings = [
        os.path.relpath(p, pkg)
        for p in sorted(pkg.parent.iterdir())
        if p.is_dir() and p != pkg and not p.name.startswith(".")
    ]
    search = os.pathsep.join([".", *siblings])
    for var in ("MYPYPATH", "PYTHONPATH"):
        env[var] = search
    relax = MYPY_TEST_RELAX if is_test_file(path) else []
    config = os.path.relpath(Path(mypy_config).resolve(), pkg)
    # S603/S607 as for `_run` above, plus: `mypy` is deliberately a bare name so
    # the CI step's PATH (which puts the PINNED mypy first) decides which binary
    # runs. Hardcoding an absolute path here would defeat that pinning, which is
    # the property this gate depends on.
    res = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "mypy",
            "--config-file",
            config,
            # After --config-file so it WINS over the baseline's 3.11 floor; see
            # interpreter_target (issue #968).
            "--python-version",
            interpreter_target(),
            "--explicit-package-bases",
            "--namespace-packages",
            *relax,
            rel,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(pkg),
        env=env,
    )
    count = 0
    for line in res.stdout.splitlines():
        match = _MYPY_ERROR_RE.match(line)
        if match is not None and Path(match.group("path")) == Path(rel):
            count += 1
    # Same shared rule as the ruff counter, with `measured` passed: mypy's exit 2
    # also covers a BLOCKING error, which still names a file and so belongs in the
    # count rather than aborting the run.
    require_tool_ran("mypy", res, measured=count)
    return count


def mypy_regression(base: str, path: str, package: str, mypy_config: str) -> str | None:
    """A no-regression message if *path* gains mypy errors vs *base*, else None.

    The base count needs the file's base content at its real path (so imports
    still resolve); the HEAD content is restored unconditionally afterwards.
    """
    head_n = mypy_errors(path, package, mypy_config)
    base_src = file_at_base(base, path)
    if base_src is None:
        # New file: every error is net-new; base count is zero.
        base_n = 0
    else:
        target = Path(path)
        head_src = target.read_text()
        try:
            target.write_text(base_src)
            base_n = mypy_errors(path, package, mypy_config)
        finally:
            target.write_text(head_src)
    if head_n > base_n:
        return (
            f"{path}: mypy +{head_n - base_n} errors (base {base_n} -> head {head_n})"
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="git ref to diff against")
    # Repeatable (Factory#384): apps/web-server holds every FastAPI route and
    # request model and was gated by nothing, which is how 133 credential-bearing
    # pydantic fields (#377) accumulated there unnoticed. Default unchanged, so
    # an existing `--package apps/backend` invocation behaves exactly as before.
    parser.add_argument("--package", action="append", dest="packages", default=None)
    parser.add_argument(
        "--mypy-config",
        default=MYPY_CONFIG_DEFAULT,
        help="mypy config file for the strict per-file gate",
    )
    parser.add_argument(
        "--no-mypy",
        action="store_true",
        help="skip the mypy no-regression gate (ruff-only)",
    )
    args = parser.parse_args()

    packages = args.packages or [PACKAGE_DEFAULT]
    files = changed_python_files(args.base, packages)
    if not files:
        print(f"ratchet: no changed Python files under {packages}; nothing to gate.")
        return 0

    print("ratchet: gating changed files:\n  " + "\n  ".join(files))

    ruff_regressions: list[str] = []
    for path in files:
        ruff_regressions.extend(regressions(args.base, path))

    mypy_regressions: list[str] = []
    if not args.no_mypy:
        for path in files:
            msg = mypy_regression(
                args.base, path, owning_package(path, packages), args.mypy_config
            )
            if msg is not None:
                mypy_regressions.append(msg)

    failed = False
    if ruff_regressions:
        failed = True
        print(
            "\nratchet FAILED: changed files gained ruff violations (shared strict bar):"
        )
        for line in ruff_regressions:
            print(f"  {line}")

    if mypy_regressions:
        failed = True
        print("\nratchet FAILED: changed files gained mypy --strict errors:")
        for line in mypy_regressions:
            print(f"  {line}")

    if failed:
        print(
            "\nFix the new violations (or clean the file further). The ratchet only "
            "blocks NET-NEW violations - pre-existing legacy in a touched file is "
            "allowed (coding-standards.md section 4.6)."
        )
        return 1

    suffix = "" if args.no_mypy else " (ruff + mypy)"
    print(f"ratchet PASSED: no changed file regressed{suffix}; new violations: none.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

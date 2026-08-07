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
double-named via the stray `apps/backend/__init__.py`. The base count is taken
by swapping the file's content to its base version in place (HEAD content is
restored afterwards, always). Errors mypy reports in OTHER files (imported
modules) are not attributed to the changed file and so never gate it.

Usage:
    python scripts/ratchet_lint.py --base <git-ref> [--package <dir>] \\
        [--mypy-config <ini>] [--no-mypy]

Exit code 0 if no changed file regressed; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Canonical shared ratchet rules, vendored byte-exact from the Factory hub
# and byte-exact drift-gated (Factory#403). scripts/ is sys.path[0] when this
# runs as a script, so the sibling import resolves without packaging.
# write_temp is deliberately NOT imported: mypy runs on the file in place here
# (see mypy_errors) and ruff is fed stdin, so nothing in this fork needs a temp
# copy any more.
from ratchet_helpers import MYPY_TEST_RELAX, is_test_file, require_tool_ran, ruff_stdin_argv

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
    return subprocess.run(cmd, capture_output=True, text=True, check=False, input=stdin)


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
        pkg for pkg in packages
        if Path(pkg) in target.parents or Path(pkg) == target.parent
    ]
    return max(matches, key=len) if matches else packages[0]


def changed_python_files(base: str, packages: list[str]) -> list[str]:
    """Python files under any of *packages* changed (added/modified) vs *base*."""
    res = _run(["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"])
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
    # The shared "did the tool actually run" rule (Factory#590). This used to be
    # four lines restated here, and in the mypy counter below, and in both halves
    # of the four sibling ratchets -- nine copies of one rule, which is why fixing
    # it once cost five PRs (PFactory#455, TFactory#951). It now lives in the
    # drift-gated canonical, so the next correction reaches every consumer.
    require_tool_ran("ruff", res)
    if not res.stdout.strip():
        return Counter()
    try:
        items = json.loads(res.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(res.stdout + res.stderr)
        # Exit with the tool's own code, not a constant: an interrupted run
        # (130) or a signal death reads differently from a config error, and
        # that distinction is the whole point of this guard.
        sys.exit(res.returncode)
    return Counter(item["code"] for item in items)


def file_at_base(base: str, path: str) -> str | None:
    res = _run(["git", "show", f"{base}:{path}"])
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
    res = subprocess.run(
        [
            "mypy",
            "--config-file",
            config,
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
        print(
            f"ratchet: no changed Python files under {packages}; nothing to gate."
        )
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

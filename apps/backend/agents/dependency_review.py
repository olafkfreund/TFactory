"""Agent-added dependency review + pinning gate (#650).

2026 supply-chain data: AI-authored projects ship more vulnerabilities and
agents add packages optimistically, often unpinned. TFactory already scans IaC
in the deploy lane, but nothing reviewed the DIFF of dependency manifests an
agent touched during a build. This module closes that gap as the 6th verdict
signal:

  1. Detection — diff the checked-out task branch (the source_branch checkout,
     #96) against the repo's base branch for dependency manifests
     (``requirements*.txt``, ``pyproject.toml``, ``package.json``,
     ``package-lock.json``, ``go.mod``, ``Cargo.toml``) and extract
     added/changed packages + constraints by parsing BOTH sides of the diff
     (base via ``git show``, head from the working tree) — no fragile
     hunk-line heuristics.
  2. Checks — deterministic and offline-first:
       a. Pinning: an added/changed Python or JS dependency must carry a
          version constraint. Unpinned = FAIL (gating).
       b. Known-vuln: the existing Trivy binary (already in the image for the
          deploy lane) in filesystem mode; a HIGH/CRITICAL vulnerability on an
          ADDED/CHANGED package = FAIL (gating). Trivy absent/erroring is an
          honest advisory ``scan_unavailable`` note, never a silent pass.
       c. Sanity heuristics (advisory, never gating in v1): edit-distance-1
          typosquat match against a curated top-package list, and a
          package-age (<30 days) registry lookup (best-effort network; any
          failure degrades silently).
  3. Verdict wiring — the block is persisted to
     ``findings/dependency_review.json``; the Triager attaches it to the
     completion envelope as ``dependency_review`` and, on a gating FAIL,
     downgrades a would-be ``success`` outcome to ``human_review`` (VAL
     honesty rule: never silently accept).

No-manifest-change tasks short-circuit to ``status="skipped"`` after a single
``git diff --name-only`` — zero behaviour change and no added latency beyond
the diff check. Everything here is best-effort: any infrastructure failure
(no git repo, no base ref) yields ``skipped`` with a reason, never a gate.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "read_dependency_review",
    "review_dependencies",
    "run_dependency_review",
]

_log = logging.getLogger(__name__)

_FINDINGS_FILE = "dependency_review.json"

# Manifest basenames the review keys on (mirrors the stack detector's language
# detection: python / javascript / go / rust).
_MANIFEST_NAMES = frozenset(
    {"pyproject.toml", "package.json", "package-lock.json", "go.mod", "Cargo.toml"}
)

# Ecosystems whose added deps MUST carry a version constraint (issue #650 2a:
# "added Python/JS deps"). go.mod / Cargo entries always carry versions.
_PINNING_ECOSYSTEMS = frozenset({"python", "npm"})

# npm constraint values that mean "whatever is newest" — treated as unpinned.
_NPM_UNPINNED = frozenset({"", "*", "latest", "x"})

# Curated high-download package names (PyPI + npm) for the edit-distance-1
# typosquat heuristic. Advisory only — a hit flags, never gates (v1).
_TOP_PACKAGES = frozenset(
    {
        # PyPI
        "requests",
        "urllib3",
        "numpy",
        "pandas",
        "boto3",
        "botocore",
        "django",
        "flask",
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "setuptools",
        "pip",
        "wheel",
        "cryptography",
        "certifi",
        "charset-normalizer",
        "idna",
        "python-dateutil",
        "pyyaml",
        "click",
        "jinja2",
        "six",
        "pytest",
        "packaging",
        "attrs",
        "rich",
        "httpx",
        "aiohttp",
        "typing-extensions",
        "scipy",
        "matplotlib",
        "pillow",
        "protobuf",
        "grpcio",
        "redis",
        "celery",
        "uvicorn",
        "gunicorn",
        "starlette",
        "psycopg2",
        "pymongo",
        "openai",
        "anthropic",
        "langchain",
        "tenacity",
        "colorama",
        "tqdm",
        # npm
        "react",
        "react-dom",
        "lodash",
        "axios",
        "express",
        "chalk",
        "vue",
        "next",
        "typescript",
        "webpack",
        "vite",
        "eslint",
        "prettier",
        "jest",
        "commander",
        "moment",
        "uuid",
        "dotenv",
        "cors",
        "debug",
        "glob",
        "semver",
        "yargs",
        "inquirer",
        "rxjs",
        "zod",
        "prisma",
        "mongoose",
        "socket.io",
        "jsonwebtoken",
        "bcrypt",
        "left-pad",
        "underscore",
        "async",
        "bluebird",
        "request",
        "minimist",
        "mkdirp",
        "rimraf",
    }
)

_AGE_ADVISORY_DAYS = 30
_AGE_LOOKUP_CAP = 5  # at most this many registry lookups per review
_AGE_LOOKUP_TIMEOUT = 3.0  # seconds per lookup


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #


def _git(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git argv
        ["git", "-C", str(project_dir), *args],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _detect_base_ref(project_dir: Path) -> str | None:
    """The ref the task branch is reviewed against — the repo's base branch.

    origin/HEAD (the remote default) -> origin/main -> origin/master ->
    local main/master. None when nothing resolves (=> review is skipped).
    """
    head = _git(project_dir, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if head.returncode == 0:
        ref = head.stdout.strip().removeprefix("refs/remotes/")
        if ref:
            return ref
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if (
            _git(project_dir, "rev-parse", "--verify", "--quiet", candidate).returncode
            == 0
        ):
            return candidate
    return None


def _is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in _MANIFEST_NAMES or (
        name.startswith("requirements") and name.endswith(".txt")
    )


def _changed_manifest_paths(project_dir: Path, base_ref: str) -> list[str] | None:
    """Repo-relative manifest paths that differ between base and HEAD.

    ``base...HEAD`` (merge-base) so only the task branch's own changes count.
    None when the diff itself fails (=> review is skipped, never gated).
    """
    diff = _git(project_dir, "diff", "--name-only", f"{base_ref}...HEAD")
    if diff.returncode != 0:
        return None
    return [p for p in diff.stdout.splitlines() if p.strip() and _is_manifest(p)]


def _read_side(project_dir: Path, base_ref: str, path: str, side: str) -> str | None:
    """File content at ``base`` (via git show) or ``head`` (working tree)."""
    if side == "base":
        shown = _git(project_dir, "show", f"{base_ref}:{path}")
        return shown.stdout if shown.returncode == 0 else None
    try:
        return (project_dir / path).read_text()
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- #
# manifest parsing — {normalized name: (constraint, ecosystem)}
# --------------------------------------------------------------------------- #

_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$")


def _norm(name: str) -> str:
    """PEP 503-style normalization (also harmless for npm names)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(req: str) -> tuple[str, str] | None:
    """One PEP 508-ish requirement string -> (normalized name, constraint)."""
    spec = req.split(";", 1)[0].split("#", 1)[0].strip()
    if not spec or spec.startswith("-"):
        return None  # pip option lines (-r, -e, --hash) / empty
    m = _REQ_RE.match(spec)
    if m is None:
        return None
    return _norm(m.group(1)), m.group(2).strip()


def _parse_requirements_txt(text: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        parsed = _parse_requirement(line)
        if parsed is not None:
            out[parsed[0]] = (parsed[1], "python")
    return out


def _parse_pyproject(text: str) -> dict[str, tuple[str, str]]:
    import tomllib  # noqa: PLC0415 - lazy; keeps both ruff configs' isort happy

    out: dict[str, tuple[str, str]] = {}
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return out
    project = doc.get("project") or {}
    reqs: list[str] = list(project.get("dependencies") or [])
    for extra in (project.get("optional-dependencies") or {}).values():
        if isinstance(extra, list):
            reqs.extend(extra)
    for req in reqs:
        if isinstance(req, str):
            parsed = _parse_requirement(req)
            if parsed is not None:
                out[parsed[0]] = (parsed[1], "python")
    # Poetry-style table: {name: "^1.2" | {"version": "..."} | {...}}
    poetry = ((doc.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if _norm(name) == "python":
            continue
        if isinstance(spec, str):
            out[_norm(name)] = (spec.strip(), "python")
        elif isinstance(spec, dict):
            # git/path/url deps are pinned by construction; use what's there.
            out[_norm(name)] = (str(spec.get("version") or "pinned-source"), "python")
    return out


def _parse_package_json(text: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return out
    if not isinstance(doc, dict):
        return out
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        block = doc.get(section)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            out[_norm(str(name))] = (str(spec).strip(), "npm")
    return out


_GO_REQ_RE = re.compile(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+(v[\w.+-]+)")


def _parse_go_mod(text: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        m = _GO_REQ_RE.match(line)
        if m is not None:
            out[m.group(1).lower()] = (m.group(2), "go")
    return out


def _parse_cargo_toml(text: str) -> dict[str, tuple[str, str]]:
    import tomllib  # noqa: PLC0415 - lazy; keeps both ruff configs' isort happy

    out: dict[str, tuple[str, str]] = {}
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return out
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        block = doc.get(section)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if isinstance(spec, str):
                out[_norm(name)] = (spec.strip(), "cargo")
            elif isinstance(spec, dict):
                out[_norm(name)] = (
                    str(spec.get("version") or "pinned-source"),
                    "cargo",
                )
    return out


def _parse_manifest(path: str, text: str) -> dict[str, tuple[str, str]]:
    """Parse one manifest into {normalized name: (constraint, ecosystem)}."""
    name = PurePosixPath(path).name
    if name.startswith("requirements") and name.endswith(".txt"):
        return _parse_requirements_txt(text)
    if name == "pyproject.toml":
        return _parse_pyproject(text)
    if name == "package.json":
        return _parse_package_json(text)
    if name == "go.mod":
        return _parse_go_mod(text)
    if name == "Cargo.toml":
        return _parse_cargo_toml(text)
    # package-lock.json: derived + exact-pinned by definition; the lockfile
    # change still triggers the Trivy scan but contributes no package rows.
    return {}


def _is_pinned(constraint: str, ecosystem: str) -> bool:
    c = constraint.strip()
    if ecosystem == "npm":
        return c.lower() not in _NPM_UNPINNED
    return bool(c)


def _extract_changes(
    project_dir: Path, base_ref: str, manifests: list[str]
) -> list[dict[str, Any]]:
    """Added/changed packages across the changed manifests (base vs HEAD)."""
    packages: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in manifests:
        head_text = _read_side(project_dir, base_ref, path, "head")
        if head_text is None:
            continue  # manifest deleted on the task branch
        base_text = _read_side(project_dir, base_ref, path, "base")
        head_pkgs = _parse_manifest(path, head_text)
        base_pkgs = _parse_manifest(path, base_text) if base_text is not None else {}
        for name, (constraint, ecosystem) in sorted(head_pkgs.items()):
            change = (
                "added"
                if name not in base_pkgs
                else ("changed" if base_pkgs[name][0] != constraint else None)
            )
            if change is None or (name, ecosystem) in seen:
                continue
            seen.add((name, ecosystem))
            packages.append(
                {
                    "name": name,
                    "ecosystem": ecosystem,
                    "manifest": path,
                    "constraint": constraint,
                    "pinned": _is_pinned(constraint, ecosystem),
                    "change": change,
                }
            )
    return packages


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def _run_trivy_fs(project_dir: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Trivy filesystem vuln scan -> (vulnerability rows, unavailable-note).

    Reuses the Trivy binary already shipped for the deploy lane. Tool absent or
    erroring => ``(None, note)`` — an honest not_run, never a silent pass.
    """
    if shutil.which("trivy") is None:
        return None, "trivy binary not available; known-vuln check not_run"
    try:
        argv = ["trivy", "fs", "--scanners", "vuln", "--severity", "HIGH,CRITICAL"]
        argv += ["--format", "json", "--quiet", str(project_dir)]
        proc = subprocess.run(  # noqa: S603 - fixed trivy argv
            argv,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"trivy fs scan failed: {exc}"
    if proc.returncode != 0:
        return None, f"trivy fs scan failed: {proc.stderr.strip()[:200]}"
    try:
        doc = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None, "trivy fs scan produced unparseable output"
    rows: list[dict[str, Any]] = []
    results = doc.get("Results") if isinstance(doc, dict) else None
    for result in results or []:
        if not isinstance(result, dict):
            continue
        for vuln in result.get("Vulnerabilities") or []:
            if isinstance(vuln, dict):
                rows.append(vuln)
    return rows, None


def _edit_distance_le_1(a: str, b: str) -> bool:
    """True when a != b and they are within one edit (insert/delete/replace)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return True


def _one_transposition(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` differ by exactly one adjacent swap."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return (
        len(diff) == 2  # noqa: PLR2004 - a swap touches exactly two positions
        and diff[1] == diff[0] + 1
        and a[diff[0]] == b[diff[1]]
        and a[diff[1]] == b[diff[0]]
    )


def _typosquat_match(name: str) -> str | None:
    if name in _TOP_PACKAGES:
        return None
    return next(
        (
            top
            for top in _TOP_PACKAGES
            if _edit_distance_le_1(name, top) or _one_transposition(name, top)
        ),
        None,
    )


def _registry_age_days(name: str, ecosystem: str) -> float | None:
    """Days since the package first appeared on its public registry.

    Best-effort network lookup (advisory heuristic only) — any failure, or a
    non-PyPI/npm ecosystem, returns None. Disable with TFACTORY_DEP_AGE_CHECK=0.
    """
    if os.environ.get("TFACTORY_DEP_AGE_CHECK", "1") == "0":
        return None
    urls = {
        "python": f"https://pypi.org/pypi/{name}/json",
        "npm": f"https://registry.npmjs.org/{name}",
    }
    url = urls.get(ecosystem)
    if url is None:
        return None
    try:
        import urllib.request  # noqa: PLC0415 - lazy; only on the advisory path

        with urllib.request.urlopen(url, timeout=_AGE_LOOKUP_TIMEOUT) as resp:  # noqa: S310
            doc = json.loads(resp.read().decode("utf-8"))
        created: str | None = None
        if ecosystem == "npm":
            created = (doc.get("time") or {}).get("created")
        else:
            uploads = [
                str(files[0]["upload_time_iso_8601"])
                for files in (doc.get("releases") or {}).values()
                if isinstance(files, list)
                and files
                and "upload_time_iso_8601" in files[0]
            ]
            created = min(uploads) if uploads else None
        if not created:
            return None
        first = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return (datetime.now(UTC) - first).total_seconds() / 86400
    except Exception:  # noqa: BLE001 - advisory heuristic must never raise
        return None


# --------------------------------------------------------------------------- #
# the review
# --------------------------------------------------------------------------- #


def _pinning_findings(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """2a: an added/changed Python/JS dep without a constraint is a gating fail."""
    return [
        {
            "severity": "fail",
            "kind": "unpinned",
            "package": pkg["name"],
            "detail": (
                f"{pkg['change']} {pkg['ecosystem']} dependency "
                f"{pkg['name']!r} in {pkg['manifest']} has no version "
                "constraint (house standard: pin added dependencies)"
            ),
        }
        for pkg in packages
        if pkg["ecosystem"] in _PINNING_ECOSYSTEMS and not pkg["pinned"]
    ]


def _vulnerability_findings(
    project_dir: Path, packages: list[dict[str, Any]], trivy: Any
) -> list[dict[str, Any]]:
    """2b: HIGH/CRITICAL vulns (Trivy fs) on the ADDED/CHANGED set gate; a scan
    that could not run is an honest advisory note, never a silent pass."""
    names = {p["name"] for p in packages}
    vulns, note = trivy(project_dir)
    if vulns is None:
        return [
            {
                "severity": "advisory",
                "kind": "scan_unavailable",
                "package": None,
                "detail": note or "trivy scan not_run",
            }
        ]
    findings: list[dict[str, Any]] = []
    for vuln in vulns:
        pkg_name = _norm(str(vuln.get("PkgName") or ""))
        if pkg_name in names:
            findings.append(
                {
                    "severity": "fail",
                    "kind": "vulnerability",
                    "package": pkg_name,
                    "detail": (
                        f"{vuln.get('VulnerabilityID')} "
                        f"({vuln.get('Severity')}) in {pkg_name} "
                        f"{vuln.get('InstalledVersion') or ''}".strip()
                    ),
                }
            )
    return findings


def _heuristic_findings(
    packages: list[dict[str, Any]], age: Any
) -> list[dict[str, Any]]:
    """The 2c sanity heuristics — advisory findings only, never gating (v1)."""
    findings: list[dict[str, Any]] = []
    lookups = 0
    for pkg in packages:
        squat = _typosquat_match(str(pkg["name"]))
        if squat is not None:
            findings.append(
                {
                    "severity": "advisory",
                    "kind": "typosquat",
                    "package": pkg["name"],
                    "detail": (
                        f"{pkg['name']!r} is one edit away from the popular "
                        f"package {squat!r} — possible typosquat"
                    ),
                }
            )
        if lookups < _AGE_LOOKUP_CAP:
            lookups += 1
            days = age(str(pkg["name"]), str(pkg["ecosystem"]))
            if days is not None and days < _AGE_ADVISORY_DAYS:
                findings.append(
                    {
                        "severity": "advisory",
                        "kind": "new_package_age",
                        "package": pkg["name"],
                        "detail": (
                            f"{pkg['name']!r} first published {days:.0f} day(s) "
                            f"ago (<{_AGE_ADVISORY_DAYS})"
                        ),
                    }
                )
    return findings


def _skipped(reason: str, base_ref: str | None = None) -> dict[str, Any]:
    return {
        "status": "skipped",
        "gating": False,
        "reason": reason,
        "base_ref": base_ref,
        "manifests": [],
        "packages": [],
        "findings": [],
    }


def review_dependencies(
    project_dir: Path,
    *,
    base_ref: str | None = None,
    trivy_fn: Any = None,
    age_fn: Any = None,
) -> dict[str, Any]:
    """Review agent-added/changed dependencies on the checked-out task branch.

    Returns the dependency_review verdict block::

        {
          "status": "pass" | "fail" | "advisory" | "skipped",
          "gating": bool,                # True iff status == "fail"
          "reason": str,
          "base_ref": str | None,
          "manifests": [repo-relative changed manifest paths],
          "packages":  [{name, ecosystem, manifest, constraint, pinned, change}],
          "findings":  [{severity: "fail"|"advisory", kind, package, detail}],
        }

    ``trivy_fn`` / ``age_fn`` are test seams for the scanner and the registry
    age lookup; production callers use the defaults.
    """
    trivy = trivy_fn if trivy_fn is not None else _run_trivy_fs
    age = age_fn if age_fn is not None else _registry_age_days

    if not (project_dir / ".git").exists():
        return _skipped(f"{project_dir} is not a git repo")
    base = base_ref or _detect_base_ref(project_dir)
    if base is None:
        return _skipped("no base branch to diff against")
    manifests = _changed_manifest_paths(project_dir, base)
    if manifests is None:
        return _skipped(f"git diff against {base} failed", base)
    if not manifests:
        return _skipped("no dependency manifest changes", base)

    packages = _extract_changes(project_dir, base, manifests)
    # 2a pinning (gating) + 2b known-vuln (gating) + 2c heuristics (advisory).
    findings = _pinning_findings(packages)
    if packages:
        findings.extend(_vulnerability_findings(project_dir, packages, trivy))
    findings.extend(_heuristic_findings(packages, age))

    fails = [f for f in findings if f["severity"] == "fail"]
    if fails:
        status = "fail"
        reason = "; ".join(str(f["detail"]) for f in fails[:3])
    elif findings:
        status = "advisory"
        reason = "advisory findings only (non-gating)"
    else:
        status = "pass"
        reason = (
            f"{len(packages)} added/changed package(s) pinned and clean"
            if packages
            else "manifest changed but no packages added/changed"
        )
    return {
        "status": status,
        "gating": status == "fail",
        "reason": reason,
        "base_ref": base,
        "manifests": manifests,
        "packages": packages,
        "findings": findings,
    }


# --------------------------------------------------------------------------- #
# spec-workspace persistence (the artifact the Triager's envelope reads)
# --------------------------------------------------------------------------- #


def run_dependency_review(spec_dir: Path, project_dir: Path) -> dict[str, Any] | None:
    """Run the review and persist ``findings/dependency_review.json``.

    Best-effort: any error returns None and never raises into the pipeline.
    """
    try:
        block = review_dependencies(project_dir)
        findings_dir = spec_dir / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        (findings_dir / _FINDINGS_FILE).write_text(json.dumps(block, indent=2))
        return block
    except Exception:  # noqa: BLE001 - the review must never break the pipeline
        _log.warning("dependency review failed (non-fatal)", exc_info=True)
        return None


def read_dependency_review(spec_dir: Path) -> dict[str, Any] | None:
    """The persisted dependency_review block, or None when it never ran."""
    try:
        path = Path(spec_dir) / "findings" / _FINDINGS_FILE
        if path.exists():
            doc = json.loads(path.read_text())
            return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
    return None

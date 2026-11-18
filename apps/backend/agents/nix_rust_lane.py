"""The Rust cargo-test Nix lane (#1578): pure helpers plus the Job runner.

rustc and cargo come from ``nix_provisioner``'s builtin ``rust`` table, so the
lane needs no toolchain descriptor; the crate is copied to a writable path and
tested there (``/work`` is read-only to the Job's uid).
"""

from __future__ import annotations

import contextlib
import itertools
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from agents import nix_env
from agents.nix_env import _shquote
from tools.runners.docker_runner import DockerRunResult

if TYPE_CHECKING:
    from agents.execution_sandbox import ExecutionSandbox


# Paths INSIDE the Job container: /tmp is the one place its non-root uid can
# write (the co-mounted /work is read-only to it), so the crate is copied and
# built here. S108 is about host temp files; these never touch the host.
_CARGO_SRC_DIR = "/tmp/tf_cargo_src"  # noqa: S108
_CARGO_HOME = "/tmp/tf_cargo_home"  # noqa: S108
_CARGO_TARGET_DIR = "/tmp/tf_cargo_target"  # noqa: S108


class CargoCounts(NamedTuple):
    passed: int
    failed: int
    ignored: int


_RESULT_RE = re.compile(
    r"(?m)^\s*test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;"
)
_TARGET_RE = re.compile(r"(?m)^\s*Running (?:.*[/\\])?(tests/[^/\\\s()]+\.rs)\s+\(")
_RUNNING_RE = re.compile(r"(?m)^\s*(?:Running|Doc-tests)\b")
_WORKSPACE_RE = re.compile(r"(?m)^\s*\[workspace\]\s*(?:#.*)?$")


def parse_cargo_test_output(text: str) -> CargoCounts:
    """Sum test counts across unit, integration, and documentation binaries."""
    counts = [0, 0, 0]
    for result in _RESULT_RE.findall(text):
        for index, value in enumerate(result):
            counts[index] += int(value)
    return CargoCounts(*counts)


def cargo_ran_targets(text: str) -> set[str]:
    """Return integration-test files in which Cargo executed at least one test.

    Cargo prints ``Running tests/<name>.rs`` even for a target whose tests are
    all ``#[ignore]``d or that holds none, so the launch line alone is not
    evidence: the target's own ``test result`` line must show a pass or a fail.
    Known limit: workspace members with the same ``tests/<name>.rs`` share one
    key, because Cargo's output carries no package name.
    """
    starts = [m.start() for m in _RUNNING_RE.finditer(text)] + [len(text)]
    ran: set[str] = set()
    for begin, end in itertools.pairwise(starts):
        segment = text[begin:end]
        target = _TARGET_RE.match(segment)
        counts = parse_cargo_test_output(segment)
        if target and counts.passed + counts.failed > 0:
            ran.add(target.group(1))
    return ran


def cargo_verdict(exit_code: int, counts: CargoCounts) -> tuple[int, str]:
    """Fail closed when a successful cargo exit lacks passing test evidence."""
    if exit_code:
        return exit_code, ""
    if counts.failed:
        return 1, f"cargo reported {counts.failed} failed test(s)"
    if counts.passed:
        return 0, ""
    if counts.ignored:
        return 125, "cargo ran only ignored tests"
    return 1, "cargo ran zero tests"


def cargo_job_script(root_dir: str, rel: str, extra_env: dict[str, str]) -> str:
    """Build a Job script that copies the whole read-only project, then tests.

    The project root is copied, not just the crate, so a path dependency such as
    ``../../libs/common`` still resolves; the script then ``cd``s into ``rel``
    (the crate directory relative to the root, ``.`` for the root itself).
    """
    exports = "".join(
        f"export {key}={_shquote(str(value))}\n" for key, value in extra_env.items()
    )
    src = _shquote(_CARGO_SRC_DIR)
    root_q = _shquote(f"{root_dir}/.")
    cd_q = _shquote(f"{_CARGO_SRC_DIR}/{rel}" if rel != "." else _CARGO_SRC_DIR)
    return (
        "#!/usr/bin/env bash\nset +e\n"
        + exports
        + f"rm -rf {src} && mkdir -p {src} && cp -r {root_q} {src} && cd {cd_q}\n"
        + f"export CARGO_HOME={_shquote(_CARGO_HOME)}\n"
        + f"export CARGO_TARGET_DIR={_shquote(_CARGO_TARGET_DIR)}\n"
        + "cargo test --workspace --no-fail-fast 2>&1\n"
        + "echo __CARGO_EXIT=$?\n"
    )


def _cargo_crate_dir(project_dir: Path, hint: Path | None) -> Path:
    """Find the enclosing crate, preferring its containing workspace root."""
    project = Path(project_dir).resolve()
    if hint is not None:
        start = Path(hint)
        start = start if start.is_absolute() else project / start
        if start.suffix:
            start = start.parent
        start = start.resolve()
        if start == project or project in start.parents:
            nearest = None
            for directory in (start, *start.parents):
                if directory != project and project not in directory.parents:
                    break
                manifest = directory / "Cargo.toml"
                if manifest.is_file():
                    if nearest is None:
                        nearest = directory
                    if _WORKSPACE_RE.search(manifest.read_text(encoding="utf-8")):
                        return directory
            if nearest is not None:
                return nearest

    manifests = sorted(
        (
            manifest.parent
            for manifest in project.rglob("Cargo.toml")
            if manifest.is_file()
        ),
        key=lambda directory: (len(directory.parts), str(directory)),
    )
    return manifests[0] if manifests else project


def rust_environment(spec_dir: Path) -> dict[str, Any]:
    """The Rust nix environment for the cargo verify lane.

    Prefer a contract ``environment`` that declares a Rust nix env; otherwise
    synthesize one naming only the language (the provisioner's builtin table
    supplies rustc + cargo). ``network`` is restricted: cargo resolves crates
    at run time.
    """
    env = nix_env.environment_from_contract(spec_dir)
    if (
        env is not None
        and nix_env.is_nix_environment(env)
        and (env.get("language") or "").lower() == "rust"
    ):
        return dict(env)
    return {
        "language": "rust",
        "toolchain": {},
        "system_packages": [],
        "verify_commands": ["cargo test --workspace --no-fail-fast"],
        "provisioning": {"method": "nix", "generated": True},
        "network": "restricted",
    }


def run_cargo_lane_via_nix(
    spec_dir: Path,
    project_dir: Path,
    *,
    hint: Path | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: int = 900,
) -> DockerRunResult | None:
    """Run the crate's tests inside the per-task Nix dev shell (#1578).

    The Rust twin of ``run_maven_lane_via_nix``: ``cargo test --workspace``
    covers the whole crate, so there is no single-file staging. The exit code
    is cargo's, tightened by :func:`cargo_verdict` (a zero exit with zero tests
    is not a pass; only ignored tests is not run). Timeout is 900 s: a cold
    crate download takes minutes.

    Returns None when the sandbox isn't configured (caller falls back).
    """
    mount = nix_env._NIX_MOUNT
    sandbox: ExecutionSandbox | None = nix_env.nix_runner_from_env()
    if sandbox is None:
        nix_env._log.info(
            "run_cargo_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping"
        )
        return None
    plan = nix_env.materialize_flake(
        spec_dir, project_dir, env=rust_environment(spec_dir)
    )
    if plan is None:
        return None

    pd = Path(project_dir)
    try:
        crate_dir = _cargo_crate_dir(pd, hint)
        rel = (
            "."
            if crate_dir == pd.resolve()
            else str(crate_dir.relative_to(pd.resolve()))
        )
        (pd / nix_env._JOB_SCRIPT).write_text(
            cargo_job_script(mount, rel, extra_env or {}), encoding="utf-8"
        )
        job_cmd = f"nix develop path:{mount}#default --command bash {mount}/{nix_env._JOB_SCRIPT}"
        res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
    finally:
        with contextlib.suppress(OSError):
            (pd / nix_env._JOB_SCRIPT).unlink(missing_ok=True)
        nix_env._drop_generated_flake(plan)

    stdout = res.stdout or ""
    code, why = cargo_verdict(
        nix_env._parse_exit_marker(stdout, "__CARGO_EXIT="),
        parse_cargo_test_output(stdout),
    )
    return DockerRunResult(
        returncode=code,
        stdout=stdout,
        stderr=why,
        junit_xml_path=None,
        coverage_xml_path=None,
        argv=["nix", "develop", f"path:{mount}#default", "--", "cargo", "test"],
    )

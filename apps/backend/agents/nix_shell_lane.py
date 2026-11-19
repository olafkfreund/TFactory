"""The shell lane (#1581): bats (TAP) and shellcheck in the per-task Nix shell.

The project's own ``flake.nix`` devShell supplies the tools when the repo owns
one; otherwise a flake is generated from ``languages/shell.yaml`` (bats,
shellcheck). A tool that is absent makes the lane ``not_run`` (126), never a
pass; an all-skipped bats run is ``not_run`` (125), never a pass or a failure.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import nix_env
from agents.nix_env import _shquote
from tools.runners.docker_runner import DockerRunResult

if TYPE_CHECKING:
    from agents.execution_sandbox import ExecutionSandbox


# A path INSIDE the Job container: /tmp is the one place its non-root uid can
# write (/work is read-only to it). S108 is about host temp files.
_BATS_TMP_TEMPLATE = "/tmp/tfactory-bats.XXXXXX"  # noqa: S108


@dataclass(frozen=True)
class TapCounts:
    passed: int = 0
    failed: int = 0
    skipped: int = 0


_TAP_RESULT = re.compile(r"^(not )?ok [0-9]+(?:\s|$)", re.IGNORECASE)
_TAP_SKIP = re.compile(r"#\s*skip\b", re.IGNORECASE)


def parse_tap(text: str) -> TapCounts:
    """Count unindented TAP results, treating skipped failures as skips."""
    passed = failed = skipped = 0
    for line in text.splitlines():
        result = _TAP_RESULT.match(line)
        if result is None:
            continue
        if _TAP_SKIP.search(line):
            skipped += 1
        elif result.group(1):
            failed += 1
        else:
            passed += 1
    return TapCounts(passed, failed, skipped)


_NOT_RUN_EXITS = (126, 127)
_UNAVAILABLE = "bats or shellcheck unavailable: lane did not run"


def _tool_problem(
    bats_exit: int | None, shellcheck_exit: int | None
) -> tuple[int, str] | None:
    """A lane that did not run, or a tool error: both beat any TAP evidence."""
    for code in (bats_exit, shellcheck_exit):
        if code is not None and code in _NOT_RUN_EXITS:
            return code, _UNAVAILABLE
    if bats_exit is None or shellcheck_exit is None:
        return 1, "missing exit marker"
    if bats_exit not in (0, 1) or shellcheck_exit not in (0, 1):
        return 1, "tool error"
    return None


def shell_verdict(
    bats_exit: int | None, shellcheck_exit: int | None, counts: TapCounts
) -> tuple[int, str]:
    """Apply lane exit precedence before interpreting TAP skips."""
    problem = _tool_problem(bats_exit, shellcheck_exit)
    if problem is not None:
        return problem
    if counts.failed or bats_exit == 1:
        return 1, "bats reported failures"
    if shellcheck_exit == 1:
        return 1, "shellcheck reported findings"
    if counts.passed == 0 and counts.skipped > 0:
        return 125, "every bats test was skipped: not run"
    if counts.passed == 0:
        return 1, "bats ran zero tests"
    return 0, ""


_FILE_EXIT = re.compile(r"^__BATS_FILE_EXIT=(\d+) (.+)$")


def _split_files(stdout: str) -> list[tuple[str, int, str]]:
    """(path, bats exit, that file's own output); output precedes its marker."""
    out: list[tuple[str, int, str]] = []
    seg: list[str] = []
    for line in stdout.splitlines():
        m = _FILE_EXIT.match(line)
        if m is None:
            seg.append(line)
            continue
        out.append((m[2], int(m[1]), "\n".join(seg)))
        seg = []
    return out


def file_results(stdout: str) -> dict[str, int]:
    """Per-file verdict (0 pass, 1 fail, 125 all skipped) from the full stdout.

    A bats tool error (exit above 1) is kept as is so callers can fail the lane.
    """
    return {
        p: rc if rc > 1 else shell_verdict(rc, 0, parse_tap(text))[0]
        for p, rc, text in _split_files(stdout)
    }


def file_output(stdout: str) -> dict[str, str]:
    """Each file's own bats output, so one file's failure evidence is not another's."""
    return {p: text for p, _rc, text in _split_files(stdout)}


def bats_job_script(run_dir: str, extra_env: dict[str, str]) -> str:
    """Build a Job script that checks tools and runs against a writable copy."""
    exports = "".join(f"export {k}={_shquote(str(v))}\n" for k, v in extra_env.items())
    run_q = _shquote(run_dir)
    return (
        "#!/usr/bin/env bash\nset +e\n"
        + exports
        + "missing=0\n"
        + "command -v bats >/dev/null 2>&1 || { echo 'bats is missing'; missing=1; }\n"
        + "command -v shellcheck >/dev/null 2>&1 || { echo 'shellcheck is missing'; missing=1; }\n"
        + 'if [ "$missing" -ne 0 ]; then\n'
        + "  echo __BATS_EXIT=126\n  echo __SHELLCHECK_EXIT=126\n  exit 0\nfi\n"
        + f"src=$(mktemp -d {_BATS_TMP_TEMPLATE}) || exit 1\n"
        + f'cp -r {run_q}/. "$src"/ && cd "$src" || '
        + "{ echo 'could not copy shell project'; echo __BATS_EXIT=1; "
        + "echo __SHELLCHECK_EXIT=1; exit 0; }\n"
        + "export BATS_TMPDIR=/tmp\n"
        + "mapfile -d '' -t bats_files < <(find . -type d -name .git -prune -o "
        + "-type f -name '*.bats' -print0 | sort -z)\n"
        + "if [ ${#bats_files[@]} -eq 0 ]; then\n"
        + "  echo 'no .bats files'\n  echo __BATS_EXIT=1\n"
        # One bats per file: a file that fails (e.g. needs nix/network) must
        # not hide the results of the others. Worst exit wins.
        + 'else\n  worst=0\n  for f in "${bats_files[@]}"; do\n'
        + '    bats --tap "$f" 2>&1\n    rc=$?\n'
        + '    echo "__BATS_FILE_EXIT=$rc $f"\n'
        + '    [ "$rc" -gt "$worst" ] && worst=$rc\n  done\n'
        + "  echo __BATS_EXIT=$worst\nfi\n"
        + "mapfile -d '' -t shell_files < <(find . -type d "
        + "\\( -name .git -o -name tests -o -name test \\) -prune -o "
        + "-type f \\( -name '*.sh' -o -name '*.bash' \\) -print0 | sort -z)\n"
        + "if [ ${#shell_files[@]} -eq 0 ]; then\n"
        + "  echo __SHELLCHECK_EXIT=0\n"
        + 'else\n  shellcheck -x -S warning "${shell_files[@]}" 2>&1\n'
        + "  echo __SHELLCHECK_EXIT=$?\nfi\n"
    )


def shell_environment(spec_dir: Path, project_dir: Path) -> dict[str, Any]:
    """The nix environment for the shell lane.

    A contract ``environment`` that declares a shell nix env wins. Otherwise,
    when the repo owns a flake, the manifest is NOT generated, so
    ``materialize_flake`` respects the project's own devShell ("inside the
    project's nix develop"); with no flake it is generated from the shell
    descriptor (bats, shellcheck).
    """
    env = nix_env.environment_from_contract(spec_dir)
    if (
        env is not None
        and nix_env.is_nix_environment(env)
        and (env.get("language") or "").lower() == "shell"
    ):
        return dict(env)
    return {
        "language": "shell",
        "toolchain": {},
        "system_packages": [],
        "verify_commands": ["bats --tap -r tests"],
        "provisioning": {
            "method": "nix",
            "generated": not nix_env.flake_is_repo_owned(project_dir),
        },
        "network": "none",
    }


def run_bats_lane_via_nix(
    spec_dir: Path,
    project_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 600,
) -> DockerRunResult | None:
    """Run the project's bats suite and shellcheck in the per-task Nix shell.

    Twin of ``run_cargo_lane_via_nix``: the whole project root is one unit (bats and
    shellcheck both read it as a tree), and :func:`shell_verdict` turns the two
    exit markers plus the TAP counts into one exit code.

    Returns None when the sandbox isn't configured (caller falls back).
    """
    mount = nix_env._NIX_MOUNT
    sandbox: ExecutionSandbox | None = nix_env.nix_runner_from_env()
    if sandbox is None:
        nix_env._log.info(
            "run_bats_lane_via_nix: TFACTORY_NIX_RUNNER_IMAGE unset; skipping"
        )
        return None
    plan = nix_env.materialize_flake(
        spec_dir, project_dir, env=shell_environment(spec_dir, project_dir)
    )
    if plan is None:
        return None

    pd = Path(project_dir)
    try:
        # Always the project root: the flake (repo-owned or generated) is
        # materialized there, so entering a nested directory's own flake would
        # never match the devShell `nix develop` enters.
        (pd / nix_env._JOB_SCRIPT).write_text(
            bats_job_script(mount, extra_env or {}), encoding="utf-8"
        )
        job_cmd = f"nix develop path:{mount}#default --command bash {mount}/{nix_env._JOB_SCRIPT}"
        res = sandbox.run([job_cmd], workdir=str(pd), timeout=timeout)
    finally:
        with contextlib.suppress(OSError):
            (pd / nix_env._JOB_SCRIPT).unlink(missing_ok=True)
        nix_env._drop_generated_flake(plan)

    stdout = res.stdout or ""
    markers: dict[str, int | None] = {}
    for prefix in ("__BATS_EXIT=", "__SHELLCHECK_EXIT="):
        has = any(ln.startswith(prefix) for ln in stdout.splitlines())
        markers[prefix] = nix_env._parse_exit_marker(stdout, prefix) if has else None
    code, why = shell_verdict(
        markers["__BATS_EXIT="], markers["__SHELLCHECK_EXIT="], parse_tap(stdout)
    )
    if why:
        # Counts come from the full TAP, not the clipped tail kept per run.
        c = parse_tap(stdout)
        bad = sorted(p for p, v in file_results(stdout).items() if v == 1)
        why += f" ({c.passed} passed, {c.failed} failed, {c.skipped} skipped"
        why += f"; failing files: {', '.join(bad)})" if bad else ")"
    return DockerRunResult(
        returncode=code,
        stdout=stdout,
        stderr=why,
        junit_xml_path=None,
        coverage_xml_path=None,
        argv=["nix", "develop", f"path:{mount}#default", "--", "bats", "--tap"],
    )

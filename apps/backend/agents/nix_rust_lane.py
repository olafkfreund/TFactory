"""Pure helpers for the Rust cargo-test Nix lane."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from agents.nix_env import _shquote


class CargoCounts(NamedTuple):
    passed: int
    failed: int
    ignored: int


_RESULT_RE = re.compile(
    r"(?m)^\s*test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;"
)
_TARGET_RE = re.compile(r"(?m)^\s*Running (?:.*[/\\])?(tests/[^/\\\s()]+\.rs)\s+\(")
_WORKSPACE_RE = re.compile(r"(?m)^\s*\[workspace\]\s*(?:#.*)?$")


def parse_cargo_test_output(text: str) -> CargoCounts:
    """Sum test counts across unit, integration, and documentation binaries."""
    counts = [0, 0, 0]
    for result in _RESULT_RE.findall(text):
        for index, value in enumerate(result):
            counts[index] += int(value)
    return CargoCounts(*counts)


def cargo_ran_targets(text: str) -> set[str]:
    """Return integration-test files Cargo says it actually ran."""
    return set(_TARGET_RE.findall(text))


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


def cargo_job_script(run_dir: str, stage_dir: str, extra_env: dict[str, str]) -> str:
    """Build a Job script that copies the read-only crate before testing."""
    exports = "".join(
        f"export {key}={_shquote(str(value))}\n" for key, value in extra_env.items()
    )
    src = _shquote("/tmp/tf_cargo_src")
    run_q = _shquote(run_dir)
    return (
        "#!/usr/bin/env bash\nset +e\n"
        + exports
        + f"rm -rf {src} && cp -r {run_q} {src} && cd {src}\n"
        + f"export CARGO_HOME={_shquote('/tmp/tf_cargo_home')}\n"
        + f"export CARGO_TARGET_DIR={_shquote('/tmp/tf_cargo_target')}\n"
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

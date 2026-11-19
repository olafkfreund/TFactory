"""Pure shell lane helpers and their generated Job script."""

from pathlib import Path

import pytest
from agents.nix_shell_lane import (
    TapCounts,
    bats_job_script,
    file_results,
    parse_tap,
    shell_verdict,
)


@pytest.mark.parametrize(
    ("tap", "expected"),
    [
        ("ok 1 first\nok 2 second", TapCounts(2, 0, 0)),
        ("not ok 1 failed", TapCounts(0, 1, 0)),
        ("ok 1 first # SKIP reason\nok 2 # skip", TapCounts(0, 0, 2)),
        ("ok 1 # skip\nnot ok 2 broken", TapCounts(0, 1, 1)),
        ("ok 1 # skip\nok 2 works", TapCounts(1, 0, 1)),
        ("TAP version 13\n1..0\n# ok 1\n  ok 2", TapCounts()),
        ("not ok 1 # skip reason", TapCounts(0, 0, 1)),
        ("not ok 1 # TODO later\nok 2 # todo", TapCounts(1, 1, 0)),
    ],
)
def test_parse_tap(tap, expected):
    assert parse_tap(tap) == expected


@pytest.mark.parametrize(
    ("bats", "shellcheck", "counts", "code", "reason"),
    [
        (126, 0, TapCounts(1), 126, "bats or shellcheck unavailable: lane did not run"),
        (127, 0, TapCounts(1), 127, "bats or shellcheck unavailable: lane did not run"),
        (0, 126, TapCounts(1), 126, "bats or shellcheck unavailable: lane did not run"),
        (0, 127, TapCounts(1), 127, "bats or shellcheck unavailable: lane did not run"),
        (None, 0, TapCounts(1), 1, "missing exit marker"),
        (0, None, TapCounts(1), 1, "missing exit marker"),
        (2, 0, TapCounts(1), 1, "tool error"),
        (0, 2, TapCounts(1), 1, "tool error"),
        (0, 0, TapCounts(0, 1, 1), 1, "bats reported failures"),
        (1, 0, TapCounts(0, 0, 1), 1, "bats reported failures"),
        (0, 1, TapCounts(0, 0, 1), 1, "shellcheck reported findings"),
        (0, 0, TapCounts(0, 0, 2), 125, "every bats test was skipped: not run"),
        (0, 0, TapCounts(), 1, "bats ran zero tests"),
        (0, 0, TapCounts(1, 0, 1), 0, ""),
    ],
)
def test_shell_verdict(bats, shellcheck, counts, code, reason):
    assert shell_verdict(bats, shellcheck, counts) == (code, reason)


def test_bats_job_script():
    script = bats_job_script("/some path/it's source", {"VALUE": "it's fine"})
    assert script.startswith("#!/usr/bin/env bash\nset +e\n")
    assert "export VALUE='it'\"'\"'s fine'" in script
    assert "cp -r '/some path/it'\"'\"'s source'/." in script
    assert "command -v bats >/dev/null 2>&1" in script
    assert "command -v shellcheck >/dev/null 2>&1" in script
    assert "command -v bats shellcheck" not in script
    assert "echo __BATS_EXIT=126" in script
    assert "echo __SHELLCHECK_EXIT=126" in script
    assert "export BATS_TMPDIR=/tmp" in script
    assert "-name .git -prune" in script
    assert "-name tests -o -name test" in script
    assert "-name '*.bats'" in script
    assert "-name '*.sh' -o -name '*.bash'" in script
    assert 'for f in "${bats_files[@]}"' in script  # one bats per file
    assert 'bats --tap "$f" 2>&1' in script
    assert 'echo "__BATS_FILE_EXIT=$rc $f"' in script
    assert "echo __BATS_EXIT=$worst" in script
    assert 'shellcheck -x -S warning "${shell_files[@]}" 2>&1' in script
    assert "no .bats files" in script
    assert "echo __SHELLCHECK_EXIT=$?" in script
    assert "echo __SHELLCHECK_EXIT=0" in script


class _FakeSandbox:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def run(self, commands, *, workdir=None, timeout=300):
        from types import SimpleNamespace

        self.commands = list(commands)
        return SimpleNamespace(stdout=self.stdout, returncode=0)


def test_run_bats_lane_returns_none_without_a_sandbox(tmp_path, monkeypatch):
    from agents import nix_shell_lane

    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    assert nix_shell_lane.run_bats_lane_via_nix(tmp_path, tmp_path) is None


def _run_with(tmp_path, monkeypatch, stdout):
    from agents import nix_env, nix_shell_lane

    sandbox = _FakeSandbox(stdout)
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: sandbox)
    res = nix_shell_lane.run_bats_lane_via_nix(tmp_path, tmp_path)
    assert res is not None
    assert "nix develop path:/work#default --command bash" in sandbox.commands[0]
    assert not (tmp_path / nix_env._JOB_SCRIPT).exists()  # script cleaned up
    return res


def test_run_bats_lane_maps_skip_to_not_run(tmp_path, monkeypatch):
    out = "ok 1 a # skip no nix\n__BATS_EXIT=0\n__SHELLCHECK_EXIT=0\n"
    res = _run_with(tmp_path, monkeypatch, out)
    assert res.returncode == 125 and "skipped" in res.stderr


def test_run_bats_lane_passes_and_fails(tmp_path, monkeypatch):
    ok = "ok 1 a\n__BATS_EXIT=0\n__SHELLCHECK_EXIT=0\n"
    assert _run_with(tmp_path, monkeypatch, ok).returncode == 0
    bad = "ok 1 a\nnot ok 2 b\n__BATS_EXIT=1\n__SHELLCHECK_EXIT=0\n"
    assert _run_with(tmp_path, monkeypatch, bad).returncode == 1
    findings = "ok 1 a\n__BATS_EXIT=0\n__SHELLCHECK_EXIT=1\n"
    assert _run_with(tmp_path, monkeypatch, findings).returncode == 1


def test_run_bats_lane_missing_tool_marker_is_not_run(tmp_path, monkeypatch):
    out = "bats is missing\n__BATS_EXIT=126\n__SHELLCHECK_EXIT=126\n"
    assert _run_with(tmp_path, monkeypatch, out).returncode == 126


def test_run_bats_lane_with_no_markers_fails_closed(tmp_path, monkeypatch):
    assert _run_with(tmp_path, monkeypatch, "ok 1 a\n").returncode == 1


def test_shell_environment_respects_a_repo_owned_flake(tmp_path, monkeypatch):
    from agents import nix_env, nix_shell_lane

    monkeypatch.setattr(nix_env, "environment_from_contract", lambda _d: None)
    monkeypatch.setattr(nix_env, "flake_is_repo_owned", lambda _p, _o=None: True)
    env = nix_shell_lane.shell_environment(tmp_path, tmp_path)
    assert env["provisioning"]["generated"] is False
    monkeypatch.setattr(nix_env, "flake_is_repo_owned", lambda _p, _o=None: False)
    env = nix_shell_lane.shell_environment(tmp_path, tmp_path)
    assert env["provisioning"]["generated"] is True and env["language"] == "shell"


def test_file_results_isolates_each_file():
    out = (
        "1..1\nnot ok 1 nix flake show\n__BATS_FILE_EXIT=1 ./test/flake.bats\n"
        "ok 1 rotates\n__BATS_FILE_EXIT=0 ./test/rotate.bats\n"
        "ok 1 a # skip x\n__BATS_FILE_EXIT=0 ./test/s.bats\n"
    )
    assert file_results(out) == {
        "./test/flake.bats": 1,
        "./test/rotate.bats": 0,
        "./test/s.bats": 125,
    }


def test_run_bats_lane_reason_has_full_counts_and_failing_files(tmp_path, monkeypatch):
    out = (
        "not ok 1 nix\n__BATS_FILE_EXIT=1 ./test/flake.bats\n"
        "ok 1 a\nok 2 b\n__BATS_FILE_EXIT=0 ./test/a.bats\n"
        "__BATS_EXIT=1\n__SHELLCHECK_EXIT=0\n"
    )
    res = _run_with(tmp_path, monkeypatch, out)
    assert res.returncode == 1
    assert (
        "2 passed, 1 failed, 0 skipped; failing files: ./test/flake.bats" in res.stderr
    )


def test_file_results_keeps_tool_errors_and_per_file_output():
    from agents.nix_shell_lane import file_output

    out = "boom\n__BATS_FILE_EXIT=2 ./a.bats\nnot ok 1 x\n__BATS_FILE_EXIT=1 ./b.bats\n"
    assert file_results(out) == {"./a.bats": 2, "./b.bats": 1}
    assert file_output(out) == {"./a.bats": "boom", "./b.bats": "not ok 1 x"}

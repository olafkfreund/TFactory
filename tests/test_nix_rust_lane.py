"""Pure cargo-test lane helpers."""

from pathlib import Path

from agents.nix_rust_lane import (
    CargoCounts,
    _cargo_crate_dir,
    cargo_job_script,
    cargo_ran_targets,
    cargo_verdict,
    parse_cargo_test_output,
)


def test_parse_cargo_test_output_across_binaries_and_doc_tests():
    output = """\
     Running unittests src/lib.rs (target/debug/deps/demo-123)
test result: ok. 3 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s
     Running tests/foo.rs (target/debug/deps/foo-123)
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
   Doc-tests demo
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""
    assert parse_cargo_test_output(output) == CargoCounts(6, 0, 1)
    assert cargo_ran_targets(output) == {"tests/foo.rs"}


def test_failed_zero_and_ignored_outputs():
    failed = "test result: FAILED. 1 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"
    zero = "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s"
    ignored = "test result: ok. 0 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out; finished in 0.00s"
    assert parse_cargo_test_output(failed) == CargoCounts(1, 2, 0)
    assert cargo_verdict(0, parse_cargo_test_output(failed))[0] == 1
    assert cargo_verdict(101, parse_cargo_test_output(failed))[0] == 101
    assert cargo_verdict(0, parse_cargo_test_output(zero)) == (
        1,
        "cargo ran zero tests",
    )
    assert cargo_verdict(0, parse_cargo_test_output(ignored))[0] == 125
    assert cargo_verdict(0, CargoCounts(1, 0, 2)) == (0, "")


def test_targets_from_workspace_paths_only():
    output = """\
     Running /work/member/tests/foo.rs (target/debug/deps/foo-123)
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
     Running tests/bar.rs (target/debug/deps/bar-123)
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
     Running unittests src/lib.rs (target/debug/deps/member-123)
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
   Doc-tests member
test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""
    assert cargo_ran_targets(output) == {"tests/foo.rs", "tests/bar.rs"}
    assert (
        cargo_ran_targets(
            "Running tests/helpers/x.rs (target/debug/deps/x)\n"
            "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured\n"
        )
        == set()
    )


def test_cargo_job_script_copies_crate_and_preserves_exit():
    script = cargo_job_script("/work/my root", "services/api", {"TOKEN": "a'b"})
    assert script.startswith("#!/usr/bin/env bash\nset +e\n")
    assert "export TOKEN='a'\"'\"'b'\n" in script
    assert (
        "rm -rf '/tmp/tf_cargo_src' && mkdir -p '/tmp/tf_cargo_src' "
        "&& cp -r '/work/my root/.' '/tmp/tf_cargo_src' "
        "&& cd '/tmp/tf_cargo_src/services/api'\n" in script
    )
    root = cargo_job_script("/work", ".", {})
    assert "&& cd '/tmp/tf_cargo_src'\n" in root
    assert "export CARGO_HOME='/tmp/tf_cargo_home'\n" in script
    assert "export CARGO_TARGET_DIR='/tmp/tf_cargo_target'\n" in script
    assert "cargo test --workspace --no-fail-fast 2>&1\n" in script
    assert "echo __CARGO_EXIT=$?\n" in script


def test_crate_dir_prefers_nearest_manifest(tmp_path: Path):
    project = tmp_path / "project"
    nested = project / "services" / "api"
    (nested / "tests").mkdir(parents=True)
    (project / "Cargo.toml").write_text("[package]\nname = 'root'\n")
    (nested / "Cargo.toml").write_text("[package]\nname = 'api'\n")
    assert _cargo_crate_dir(project, nested / "tests" / "api.rs") == nested
    assert _cargo_crate_dir(project, None) == project


def test_crate_dir_prefers_workspace_root(tmp_path: Path):
    project = tmp_path / "project"
    member = project / "crates" / "member"
    member.mkdir(parents=True)
    (project / "Cargo.toml").write_text("[workspace]\nmembers = ['crates/member']\n")
    (member / "Cargo.toml").write_text("[package]\nname = 'member'\n")
    assert _cargo_crate_dir(project, member / "tests" / "member.rs") == project
    assert _cargo_crate_dir(project, Path("crates/member/src/lib.rs")) == project


def test_crate_dir_fallback_stays_in_project(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    assert _cargo_crate_dir(project, None) == project
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Cargo.toml").write_text("[package]\nname = 'outside'\n")
    assert _cargo_crate_dir(project, outside / "src/lib.rs") == project

    nested = project / "nested"
    nested.mkdir()
    (nested / "Cargo.toml").write_text("[package]\nname = 'nested'\n")
    assert _cargo_crate_dir(project, outside / "src/lib.rs") == nested


class _FakeSandbox:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def run(self, commands, *, workdir=None, timeout=300):
        from types import SimpleNamespace

        self.commands = list(commands)
        return SimpleNamespace(stdout=self.stdout, returncode=0)


def test_run_cargo_lane_returns_none_without_a_sandbox(tmp_path, monkeypatch):
    from agents import nix_rust_lane

    monkeypatch.delenv("TFACTORY_NIX_RUNNER_IMAGE", raising=False)
    assert nix_rust_lane.run_cargo_lane_via_nix(tmp_path, tmp_path) is None


def test_run_cargo_lane_applies_the_evidence_rule(tmp_path, monkeypatch):
    from agents import nix_env, nix_rust_lane

    zero = (
        "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured\n__CARGO_EXIT=0\n"
    )
    sandbox = _FakeSandbox(zero)
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: sandbox)
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'demo'\n")
    res = nix_rust_lane.run_cargo_lane_via_nix(tmp_path, tmp_path)
    assert res is not None
    assert res.returncode == 1 and "zero tests" in res.stderr
    assert "nix develop path:/work#default --command bash" in sandbox.commands[0]
    assert not (tmp_path / nix_env._JOB_SCRIPT).exists()  # script cleaned up
    assert not (tmp_path / "flake.nix").exists()  # generated flake dropped


def test_run_cargo_lane_passes_on_real_tests(tmp_path, monkeypatch):
    from agents import nix_env, nix_rust_lane

    ok = "test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured\n__CARGO_EXIT=0\n"
    monkeypatch.setattr(nix_env, "nix_runner_from_env", lambda: _FakeSandbox(ok))
    res = nix_rust_lane.run_cargo_lane_via_nix(tmp_path, tmp_path)
    assert res is not None and res.returncode == 0


def test_rust_environment_prefers_a_contract_rust_env(tmp_path, monkeypatch):
    from agents import nix_env, nix_rust_lane

    contract = {"language": "rust", "provisioning": {"method": "nix"}, "x": 1}
    monkeypatch.setattr(nix_env, "environment_from_contract", lambda _d: contract)
    assert nix_rust_lane.rust_environment(tmp_path)["x"] == 1
    monkeypatch.setattr(nix_env, "environment_from_contract", lambda _d: None)
    env = nix_rust_lane.rust_environment(tmp_path)
    assert env["language"] == "rust" and env["network"] == "restricted"


def test_a_target_of_only_ignored_tests_is_not_evidence():
    """Cargo prints `Running tests/x.rs` even when every test is #[ignore]d."""
    out = """\
     Running unittests src/lib.rs (target/debug/deps/demo-1)
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
     Running tests/skipped.rs (target/debug/deps/skipped-1)
test result: ok. 0 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out
     Running tests/empty.rs (target/debug/deps/empty-1)
test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
     Running tests/real.rs (target/debug/deps/real-1)
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
"""
    assert cargo_ran_targets(out) == {"tests/real.rs"}
    failing = (
        "     Running tests/bad.rs (target/debug/deps/bad-1)\n"
        "test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured\n"
    )
    assert cargo_ran_targets(failing) == {"tests/bad.rs"}

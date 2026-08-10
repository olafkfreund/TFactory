"""The tfactory/coverage commit status must be terminal AND must be a gate.

Two defects, one step.

`pending` means "still working". Nothing re-ran `pr-review-tests.yml`, so a PR
that would never have a figure carried a check that could not complete, and
merges were forced through with `--admin` (#853). Posting *nothing* is the same
stall wearing a different hat: GitHub reports a commit with zero statuses as
combined state `pending`, and a stale `pending` is never overwritten by silence.

Fixing that made the status terminal and honest -- and left it `success` for
every pull request regardless of what its coverage did, because the figure it
published came from a regression run that never happened (#861). A terminal,
truthful "I did not check" is not a gate. So the step now reports the verdict of
a measurement taken in the same job, and a drop comes out `failure`.

Rather than assert on the YAML text, these tests EXECUTE the step's shell script
with a fake `gh` on PATH and inspect what it would post.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "pr-review-tests.yml"
)

# Values the real workflow injects via ${{ }}. The step under test must read
# everything else from the environment, so the substitution list stays short.
_CONTEXT = {
    "secrets.GITHUB_TOKEN": "fake-token",
    "secrets.TFACTORY_TOKEN": "fake-token",
    "github.repository": "olafkfreund/TFactory",
    "github.server_url": "https://github.com",
    "github.run_id": "12345",
    "github.event.pull_request.head.sha": "d34db33f",
    "github.event.pull_request.number": "820",
    # The steps read these instead of the PR event directly, so that the same
    # job works under workflow_dispatch, where there IS no pull_request
    # context. The resolve step produces them from either trigger.
    "steps.pr.outputs.sha": "d34db33f",
    "steps.pr.outputs.number": "820",
    "steps.pr.outputs.base_sha": "ba5eba5e",
    "vars.TFACTORY_URL": "https://tfactory.example",
}

_JOB = "coverage-comment"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _step(name: str) -> dict:
    """Return the named step of the coverage job."""
    for step in _workflow()["jobs"][_JOB]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {WORKFLOW}")


def _expand(text: str, context: dict[str, str]) -> str:
    """Resolve ``${{ expr }}`` the way Actions would, for the keys we supply."""
    for expr, value in context.items():
        for spacing in (f"${{{{ {expr} }}}}", f"${{{{{expr}}}}}"):
            text = text.replace(spacing, value)
    assert "${{" not in text, (
        f"unresolved Actions expression in the step; add it to _CONTEXT:\n{text}"
    )
    return text


def _run_status_step(
    tmp_path: Path, *, passed: str, description: str = ""
) -> list[list[str]]:
    """Execute the 'Set commit status' step; return the argv of each `gh` call."""
    step = _step("Set commit status")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    calls_file = tmp_path / "gh-calls.txt"
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "{ for a in \"$@\"; do printf '%s\\n' \"$a\"; done; "
        "printf '%s\\n' '--END-OF-CALL--'; } >> \"$GH_CALLS\"\n"
    )
    fake_gh.chmod(0o755)

    context = dict(_CONTEXT)
    context["steps.coverage.outputs.passed"] = passed
    context["steps.coverage.outputs.description"] = description

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GH_CALLS"] = str(calls_file)
    env["GITHUB_OUTPUT"] = str(tmp_path / "github_output")
    # The step reads the gate result from its own `env:` block. Resolve the
    # ${{ }} in those values and export them, so the shell body stays testable.
    for key, value in (step.get("env") or {}).items():
        env[key] = _expand(str(value), context)

    script = _expand(step["run"], context)
    # GitHub runs `run:` blocks as `bash -e {0}`.
    result = subprocess.run(  # noqa: S603 — fixed argv, the script is our own workflow
        [shutil.which("bash") or "/bin/bash", "-e"],
        input=script,
        text=True,
        capture_output=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, (
        f"the step exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    if not calls_file.exists():
        return []
    calls: list[list[str]] = []
    argv: list[str] = []
    for line in calls_file.read_text().splitlines():
        if line == "--END-OF-CALL--":
            calls.append(argv)
            argv = []
        else:
            argv.append(line)
    return calls


def _posted(calls: list[list[str]]) -> dict[str, str]:
    """Flatten the `-f key=value` pairs of the single posted status."""
    assert len(calls) == 1, f"expected exactly one gh call, got {calls}"
    fields: dict[str, str] = {}
    argv = calls[0]
    for i, arg in enumerate(argv):
        if arg == "-f" and i + 1 < len(argv) and "=" in argv[i + 1]:
            key, _, value = argv[i + 1].partition("=")
            fields[key] = value
    return fields


# ── the gate: a drop must be red ─────────────────────────────────────────


def test_a_drop_posts_failure(tmp_path: Path) -> None:
    """#861: the half that cannot be satisfied by making the gate permissive."""
    fields = _posted(
        _run_status_step(
            tmp_path,
            passed="false",
            description="Coverage dropped 3.50 pts: 81.50% -> 78.00%",
        )
    )
    assert fields["state"] == "failure", (
        f"a measured coverage drop posted {fields['state']!r}; every TFactory PR "
        "passed for months exactly because this was hard-coded to success"
    )
    assert "dropped 3.50 pts" in fields["description"]


def test_no_drop_posts_success_with_a_real_percentage(tmp_path: Path) -> None:
    fields = _posted(
        _run_status_step(
            tmp_path, passed="true", description="Coverage 81.50% (unchanged vs base 81.50%)"
        )
    )
    assert fields["state"] == "success"
    assert "81.50%" in fields["description"]


def test_a_gate_that_could_not_measure_is_not_a_pass(tmp_path: Path) -> None:
    """An unmeasurable run must not be reported as clean. Conflating "no result"
    with "no problem" is the whole of #861."""
    fields = _posted(_run_status_step(tmp_path, passed="", description=""))
    assert fields["state"] == "failure"
    assert fields["description"].strip(), "posted an empty description"
    assert "did not complete" in fields["description"]


# ── the earlier defect: the status must always be terminal ───────────────


def test_status_is_never_pending(tmp_path: Path) -> None:
    """The regression guard for #853, across every input."""
    cases = (
        ("true", "Coverage 81.50% (unchanged vs base 81.50%)"),
        ("false", "Coverage dropped 3.50 pts: 81.50% -> 78.00%"),
        ("false", "Coverage gate failed: base checkout failed"),
        ("", ""),
    )
    for i, (passed, description) in enumerate(cases):
        calls = _run_status_step(
            tmp_path / f"case-{i}", passed=passed, description=description
        )
        assert calls, f"no commit status was posted for {passed!r}/{description!r}"
        for argv in calls:
            assert "state=pending" not in " ".join(argv), (
                f"posted a pending status for passed={passed!r}: {argv}"
            )


def test_the_status_step_runs_even_when_the_gate_failed() -> None:
    """`continue-on-error` on the measure step is what keeps a red gate from
    skipping the status post and leaving the commit at `pending` (#853)."""
    assert _step("Measure coverage on head and base")["continue-on-error"] is True
    assert _step("Set commit status")["if"] == "always()"


def test_a_red_gate_also_fails_the_job() -> None:
    """continue-on-error must not leave the workflow itself green."""
    step = _step("Fail the job when coverage regressed")
    assert "steps.coverage.outcome != 'success'" in step["if"]
    assert "exit 1" in step["run"]


@pytest.mark.parametrize("field", ["state", "context", "description"])
def test_posted_status_is_well_formed(tmp_path: Path, field: str) -> None:
    fields = _posted(
        _run_status_step(tmp_path, passed="true", description="Coverage 81.50%")
    )
    assert fields.get(field), f"missing -f {field} in the posted status"
    assert fields["context"] == "tfactory/coverage"


# ── the gate measures; it does not ask ───────────────────────────────────


def test_the_gate_measures_both_sides_itself() -> None:
    """The old step read a figure from the cluster that nothing ever wrote. The
    verdict must come from a measurement taken here, against the PR base."""
    run = _step("Measure coverage on head and base")["run"]
    assert "scripts/coverage_gate.py" in run
    assert "steps.pr.outputs.base_sha" in run, (
        "the gate must compare against the base commit; without it there is "
        "nothing to drop from"
    )


def test_the_base_commit_is_resolved_for_both_triggers() -> None:
    """workflow_dispatch carries no pull_request context, so a dispatched run
    would compare against an empty base and pass by construction."""
    run = _step("Resolve the pull request")["run"]
    assert "baseRefOid" in run, "no base sha is resolved on workflow_dispatch"
    assert 'if [ -z "$NUM" ] || [ -z "$SHA" ] || [ -z "$BASE" ]' in run, (
        "an unresolved base must abort, not silently gate against nothing"
    )


def test_the_checkout_has_the_history_the_base_needs() -> None:
    """A shallow clone cannot produce a worktree at the base commit."""
    for step in _workflow()["jobs"][_JOB]["steps"]:
        if str(step.get("uses", "")).startswith("actions/checkout"):
            assert step["with"]["fetch-depth"] == 0
            return
    raise AssertionError("the job does not check the repository out")


def test_recording_the_figure_cannot_decide_the_verdict() -> None:
    """The POST to TFactory fills the ledger /api/coverage serves. If a cluster
    outage could turn a measured drop green, the gate would depend on exactly
    the state whose absence hid this bug for months."""
    step = _step("Record the figure in TFactory")
    body = step["run"]
    assert "::warning::" in body, "a failed record must be reported, not swallowed"
    assert "exit 1" not in body, "recording must not be able to fail the job"
    # It runs before the status step, so it must not be able to abort the job.
    assert "set -e\n" not in body and "set -euo" not in body


def test_the_pr_comment_reports_both_sides() -> None:
    body = _step("Post or update coverage comment")["with"]["body"]
    assert "head_pct" in body and "base_pct" in body
    assert "outputs.pct }}%" not in body, (
        "the comment appends '%' to a raw output, rendering 'N/A%' when there "
        "is no figure"
    )
    assert "may still be running" not in body, (
        "a PR whose gate did not complete has no pipeline running"
    )

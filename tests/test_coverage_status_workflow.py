"""The tfactory/coverage commit status must always reach a terminal state — #853.

`pending` means "still working". Nothing re-runs `pr-review-tests.yml` (it fires
only on pull_request opened/synchronize), so a PR that will never have a
regression run — a dependabot bump, a docs-only change — carried a check that
could not complete, and merges were forced through with `--admin`.

Posting *nothing* is the same stall wearing a different hat: GitHub reports a
commit with zero statuses as combined state `pending`, and a stale `pending`
left by an earlier run is never overwritten by silence.

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
    "github.repository": "olafkfreund/TFactory",
    "github.event.pull_request.head.sha": "d34db33f",
    "vars.TFACTORY_URL": "https://tfactory.example",
}


def _step(name: str) -> dict:
    """Return the named step of the coverage-comment job."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["coverage-comment"]["steps"]
    for step in steps:
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
    tmp_path: Path, *, pct: str, reason: str = "", tfactory_url: str | None = None
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
    if tfactory_url is not None:
        context["vars.TFACTORY_URL"] = tfactory_url
    # Supplied both ways on purpose: the step may read the fetch results through
    # ${{ }} interpolation or through its `env:` block. The test asserts on what
    # gets posted, not on which of the two the step happens to use.
    context["steps.coverage.outputs.pct"] = pct
    context["steps.coverage.outputs.reason"] = reason

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["GH_CALLS"] = str(calls_file)
    env["GITHUB_OUTPUT"] = str(tmp_path / "github_output")
    # The step reads the coverage result from its own `env:` block. Resolve the
    # ${{ }} in those values and export them, so the shell body stays testable.
    for key, value in (step.get("env") or {}).items():
        env[key] = _expand(str(value), context)
    # Both are step outputs in the real run; the test drives them directly.
    env["PCT"] = pct
    env["REASON"] = reason

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


# ── the bug: an honest "no data" must not stall the PR ───────────────────


def test_no_coverage_still_posts_a_terminal_status(tmp_path: Path) -> None:
    """#853: a PR that will never have a regression run must still get a status.

    Silence is not neutral here — a commit with no statuses reads as `pending`,
    and a stale `pending` from before this fix is never cleared.
    """
    calls = _run_status_step(
        tmp_path,
        pct="N/A",
        reason="no regression run has recorded coverage for d34db33f",
    )
    assert calls, "no commit status was posted when coverage was unavailable"
    fields = _posted(calls)
    assert fields["state"] == "success", (
        f"expected a terminal state, got {fields['state']!r} — `pending` and "
        "silence both leave the PR unmergeable"
    )
    assert fields["context"] == "tfactory/coverage"


def test_no_coverage_description_carries_the_endpoints_reason(
    tmp_path: Path,
) -> None:
    """The endpoint states WHY there is no figure (#852); publish that, not a
    bare 'not yet available' that implies a run is still in flight."""
    reason = "no TFactory project has a checkout of olafkfreund/TFactory"
    fields = _posted(_run_status_step(tmp_path, pct="N/A", reason=reason))
    assert reason in fields["description"], (
        f"the reason was dropped from the status description: {fields['description']!r}"
    )


def test_missing_reason_says_so_rather_than_claiming_a_run_is_pending(
    tmp_path: Path,
) -> None:
    """An unreachable endpoint yields `{}` — no pct AND no reason. The status
    must still be terminal and must not imply work is in progress."""
    fields = _posted(_run_status_step(tmp_path, pct="N/A", reason=""))
    assert fields["state"] == "success"
    assert fields["description"].strip(), "posted an empty description"
    assert "not yet" not in fields["description"].lower(), (
        "an unreachable endpoint is not a run in flight"
    )


def test_status_is_never_pending(tmp_path: Path) -> None:
    """The regression guard for the original defect, across every input."""
    for pct, reason in (("N/A", "no run"), ("N/A", ""), ("81.5", ""), ("0", "")):
        calls = _run_status_step(tmp_path / f"case-{pct}-{len(reason)}", pct=pct,
                                 reason=reason)
        for argv in calls:
            assert "state=pending" not in " ".join(argv), (
                f"posted a pending status for pct={pct!r}: {argv}"
            )


def test_unset_tfactory_url_warns_but_still_reports(tmp_path: Path) -> None:
    """A missing TFACTORY_URL is a CI-config fault, not the PR's fault: warn
    loudly in the log, but do not leave the PR wedged behind a stuck check."""
    calls = _run_status_step(tmp_path, pct="N/A", reason="", tfactory_url="")
    assert calls, "no status posted when TFACTORY_URL is unset"
    assert _posted(calls)["state"] == "success"


# ── the measured path must be unchanged ──────────────────────────────────


def test_real_coverage_still_reports_the_percentage(tmp_path: Path) -> None:
    fields = _posted(_run_status_step(tmp_path, pct="81.5"))
    assert fields["state"] == "success"
    assert "81.5" in fields["description"]


def test_reason_is_exported_by_the_fetch_step() -> None:
    """The status step can only publish the reason if the fetch step emits it."""
    run = _step("Fetch coverage from TFactory")["run"]
    assert "reason=" in run, (
        "the fetch step logs .reason but never writes it to GITHUB_OUTPUT, so "
        "the commit status cannot state why coverage is missing"
    )


def test_pr_comment_does_not_render_a_percentage_it_does_not_have() -> None:
    """The same defect one line over: the comment printed `N/A%` whenever the
    figure was missing, and blamed a pipeline that "may still be running"."""
    body = _step("Post or update coverage comment")["with"]["body"]
    assert "outputs.pct }}%" not in body, (
        "the comment appends '%' to the raw output, rendering 'N/A%' when there "
        "is no coverage"
    )
    assert "may still be running" not in body, (
        "a PR that will never trigger a regression run has no pipeline running"
    )


@pytest.mark.parametrize("field", ["state", "context", "description"])
def test_posted_status_is_well_formed(tmp_path: Path, field: str) -> None:
    fields = _posted(_run_status_step(tmp_path, pct="N/A", reason="nothing recorded"))
    assert fields.get(field), f"missing -f {field} in the posted status"

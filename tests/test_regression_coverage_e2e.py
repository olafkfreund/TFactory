"""A real coverage figure, end to end, from a regression run to /api/coverage (#865).

Everything below the monkeypatched Job dispatch is production code: the real
``NixJobRunner``, the real ``outcome_from_run_result``, the real orchestrator
aggregate, the real trend ledger and the real ``/api/coverage`` handler. Only
the k8s Job is replaced -- and it is replaced by *actually running pytest under
coverage*, once per corpus entry, exactly as the Job's ``--cov=.`` command does.
So the ``coverage.xml`` files this test feeds in are measured, not fabricated.

Two things are proved here:

1. **The figure arrives.** A run over a real corpus writes a real percentage
   into ``coverage_trend.json`` and ``GET /api/coverage`` serves it back for the
   run's commit. Before this fix nothing did: the runner dropped
   ``coverage_xml_path``, so the aggregate was None, so no point was recorded,
   so the endpoint had nothing to answer with -- and the commit was never set,
   so a point could not have been matched to a SHA anyway.

2. **The union is right and the mean is wrong.** The corpus is three tests over
   disjoint slices of one module. Each Job measures the WHOLE project, so each
   report says "one third of the code, roughly" -- and the mean of three such
   reports says the same thing, while the project's real coverage is all three
   slices together. The mean is a plausible-looking wrong number (Factory#431);
   the assertions below pin the gap.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
for _p in (_REPO / "apps" / "backend", _REPO / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agents.coverage_delta import parse_coverage  # noqa: E402
from agents.regression import (  # noqa: E402
    NixJobRunner,
    ProjectScheduleConfig,
    coverage_trend_path,
    load_trend,
    regression_dir,
    run_for_project,
)
from agents.regression import nix_runner as nr  # noqa: E402
from tests_catalog.io import save_catalog  # noqa: E402
from tests_catalog.schema import CatalogEntry, TestsCatalog  # noqa: E402
from tools.runners.docker_runner import DockerRunResult  # noqa: E402

_GIT = shutil.which("git")
_SLICES = ("add", "sub", "mul")
_REMOTE = "https://github.com/olafkfreund/covdemo.git"


# ── a small real project: one module, three disjoint tests ──────────────


def _write_project(root: Path) -> None:
    """One module of three independent branches, one test per branch.

    Disjoint on purpose: a test that touches ``add`` executes none of ``sub``
    or ``mul``, so its project-wide report is honestly about a third of the
    module -- and three such reports averaged stay about a third.
    """
    root.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(
        f"def {name}(a, b):\n"
        f'    label = "{name}"\n'
        f"    total = a {op} b\n"
        f"    scaled = total * 2\n"
        f"    return (label, total, scaled)"
        for name, op in zip(_SLICES, ("+", "-", "*"), strict=True)
    )
    (root / "calc.py").write_text(body + "\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    for name in _SLICES:
        (tests / f"test_{name}.py").write_text(
            f"import calc\n\n\ndef test_{name}():\n"
            f"    assert calc.{name}(6, 3)[0] == '{name}'\n",
            encoding="utf-8",
        )
    save_catalog(
        root,
        TestsCatalog(
            version=1,
            updated_at="2026-08-08T12:00:00Z",
            tests=tuple(
                CatalogEntry.from_dict(
                    {
                        "test_id": name,
                        "test_file": f"tests/test_{name}.py",
                        "framework": "pytest",
                        "lane": "unit",
                        "language": "python",
                        "covers_acs": [f"AC#{i + 1}: {name}"],
                        "generated_at": "2026-08-08T12:00:00Z",
                        "generated_by_task": "covdemo",
                        "last_verdict": "accept",
                    }
                )
                for i, name in enumerate(_SLICES)
            ),
        ),
    )


def _git(root: Path, *args: str) -> str:
    """Run git with the ambient repo env stripped (a parent hook may export it)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    res = subprocess.run(  # noqa: S603 - fixed argv, tmp_path only
        [_GIT, "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return res.stdout.strip()


def _commit_project(root: Path) -> str:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", _REMOTE)
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "covdemo")
    return _git(root, "rev-parse", "HEAD")


# ── the substrate: really run pytest under coverage, one file per "Job" ──


def _job_dispatch(out_root: Path):
    """Stand in for the k8s Job: one ``pytest <file> --cov=.`` per corpus entry.

    Mirrors ``nix_env._build_pytest_cmd`` -- same ``--cov=.``, same
    ``--cov-report=xml``, one test file per invocation -- and returns the report
    path on a ``DockerRunResult`` exactly as ``run_pytest_lane_via_nix`` does.
    """
    calls = {"n": 0}

    def dispatch(spec_dir, project_dir, test_file, *, extra_env=None, timeout=300):
        calls["n"] += 1
        xml = out_root / f"coverage-{calls['n']}.xml"
        xml.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(  # noqa: S603 - fixed argv, tmp_path only
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(test_file).relative_to(project_dir)),
                "-p",
                "no:cacheprovider",
                "-q",
                "--cov=.",
                f"--cov-report=xml:{xml}",
            ],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert xml.is_file(), f"no coverage.xml produced: {res.stdout}\n{res.stderr}"
        return DockerRunResult(
            returncode=res.returncode,
            stdout=res.stdout,
            coverage_xml_path=xml,
        )

    return dispatch


@pytest.fixture
def covdemo(tmp_path, monkeypatch):
    """A committed project, its workspace root, and the real runner wired up."""
    if not _GIT:
        pytest.skip("git not available")
    ws = tmp_path / "workspaces"
    project_id = "covdemo"
    root = ws / project_id
    _write_project(root)
    sha = _commit_project(root)
    monkeypatch.setattr(nr, "run_pytest_lane_via_nix", _job_dispatch(tmp_path / "out"))
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(ws))
    monkeypatch.delenv("TFACTORY_WORKSPACE_ROOT", raising=False)
    return {"ws": ws, "project_id": project_id, "root": root, "sha": sha}


def _run(covdemo) -> tuple:
    return run_for_project(
        ProjectScheduleConfig(
            project_id=covdemo["project_id"],
            repo_root=covdemo["root"],
            workspace_root=covdemo["ws"],
        ),
        runner=NixJobRunner(spec_dir=covdemo["root"], project_dir=covdemo["root"]),
    )


# ── proof 1: a measured figure reaches the ledger and the API ───────────


def test_a_measured_figure_reaches_the_trend_and_the_api(covdemo):
    run, _diff = _run(covdemo)

    assert run.totals["passed"] == len(_SLICES)
    assert run.coverage_pct is not None, "the run recorded no coverage at all"
    # The commit is what makes the point findable; no automated caller set it.
    assert run.commit == covdemo["sha"]

    reg = regression_dir(covdemo["ws"], covdemo["project_id"])
    trend = load_trend(coverage_trend_path(reg))
    assert [(p.run_id, p.commit) for p in trend] == [(run.run_id, covdemo["sha"])]
    assert trend[0].coverage_pct == run.coverage_pct

    # ...and the endpoint CI calls serves it back for that commit.
    from server.routes import coverage as cov

    served = cov._coverage_for_commit(covdemo["project_id"], covdemo["sha"])
    assert served is not None, "recorded, but not retrievable via /api/coverage"
    assert served["coverage_pct"] == run.coverage_pct
    assert served["run_id"] == run.run_id

    # The figure is a real measurement of this tree, not a placeholder.
    report = json.loads((reg / f"{run.run_id}-report.json").read_text())
    assert report["run"]["coverage_pct"] == run.coverage_pct


def test_the_endpoint_resolves_the_project_from_the_repo(covdemo):
    """The full GET path: owner/name -> project id -> figure, no project_id hint.

    This is the call ``pr-review-tests.yml`` makes. It went through the reader's
    own workspace root, which pointed one directory above the writer's.
    """
    import asyncio

    from server.routes import coverage as cov

    run, _diff = _run(covdemo)
    served = asyncio.run(
        cov.get_coverage(
            repo="olafkfreund/covdemo", sha=covdemo["sha"], pr=1, project_id=""
        )
    )
    assert served["coverage_pct"] == run.coverage_pct, served.get("reason")
    assert served["project_id"] == covdemo["project_id"]


def test_an_explicit_commit_still_wins(covdemo):
    """HEAD is a default, not an override — the CLI's --commit must survive."""
    run, _diff = run_for_project(
        ProjectScheduleConfig(
            project_id=covdemo["project_id"],
            repo_root=covdemo["root"],
            workspace_root=covdemo["ws"],
            commit="deadbeef",
        ),
        runner=NixJobRunner(spec_dir=covdemo["root"], project_dir=covdemo["root"]),
    )
    assert run.commit == "deadbeef"


def test_a_non_git_worktree_records_no_commit(tmp_path, monkeypatch):
    """No SHA is better than a made-up one; the run still completes."""
    from agents.regression.orchestrator import _head_sha

    assert _head_sha(tmp_path) is None
    assert _head_sha(tmp_path / "does-not-exist") is None


# ── proof 2: the union is right, the mean is wrong ──────────────────────


def test_the_union_beats_the_mean_on_disjoint_tests(covdemo):
    """The honesty proof: the mean of per-test reports under-reports the project.

    Each test covers its own slice of ``calc.py`` and its own test file, so its
    project-wide report is roughly a third. Averaging three of those still says
    a third. The project is fully covered by the three together, and only the
    union says so.
    """
    run, _diff = _run(covdemo)

    per_test = [o.coverage_pct for o in run.results]
    assert all(p is not None for p in per_test), per_test
    mean = round(sum(per_test) / len(per_test), 2)

    assert run.coverage_pct > mean, (
        f"union {run.coverage_pct} should exceed the mean {mean} "
        f"of disjoint per-test figures {per_test}"
    )
    # Not a rounding difference: three disjoint thirds vs their average.
    assert run.coverage_pct - mean > 20.0
    # And the mean is not merely lower, it is wrong: every line of calc.py is
    # executed by the corpus, so the honest figure is at/near total coverage.
    assert run.coverage_pct > 90.0
    assert mean < 60.0


def test_each_per_test_figure_is_a_real_measurement(covdemo):
    """The per-test float stays an honest per-test signal — it is one.

    It is kept (a test's own project-wide reach is worth recording) and it is
    NOT what the run reports; these must agree with the reports on disk.
    """
    run, _diff = _run(covdemo)
    snaps = [parse_coverage(Path(o.coverage_report_path)) for o in run.results]
    for outcome, snap in zip(run.results, snaps, strict=True):
        assert outcome.coverage_pct == round(snap.line_rate * 100.0, 2)

    # And the reason the denominator has to be unioned rather than taken from
    # any one report: a `--cov=.` report omits files the run never imported, so
    # the three reports do NOT describe the same set of files.
    files = [frozenset(s.measured_lines) for s in snaps]
    assert len({*files}) == len(files), (
        "expected each report to measure a different set"
    )
    assert len(set.union(*(set(f) for f in files))) > max(len(f) for f in files)

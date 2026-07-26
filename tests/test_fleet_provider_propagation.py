"""TFactory posts its verdict to the tenant's host (RFC-0020 3.5, Factory#366).

The bug: a GitLab tenant's PARR run ended on GitHub. TFactory's share of that is
the verdict — the Triager posts its report to the pull request through
``tools/pr_comment.py``, which shells out to ``gh pr comment``. Nothing asked
which host the work was on, so a GitLab tenant's verify either posted nowhere or
errored against a repo slug ``gh`` could not resolve, AFTER the verification had
run and produced a report worth reading.

Covered here:

* the reference round-trips ``gitlab:group/project`` unchanged;
* an ingest records the declared host and a BARE repo slug on source.json;
* the Triager's gh-CLI PR comment is SKIPPED off GitHub, and the report is
  written to disk rather than lost;
* a GitHub tenant still gets its comment, unchanged;
* a spec written before this phase (no ``provider`` key) reads as GitHub.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _seam in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_seam) not in sys.path:
        sys.path.insert(0, str(_seam))

from repo_ref import (  # noqa: E402
    is_github,
    parse_repo_ref,
    project_of,
    provider_of,
    qualify_repo,
)

_GL_REF = "gitlab:platform/pipelines"
_GH_REF = "acme/widgets"


# ── the reference ────────────────────────────────────────────────────────────


def test_the_contract_round_trips_a_gitlab_reference_unchanged():
    assert parse_repo_ref(_GL_REF) == ("gitlab", "platform/pipelines")
    assert qualify_repo(*parse_repo_ref(_GL_REF)) == _GL_REF


def test_github_is_the_unqualified_default():
    assert parse_repo_ref(_GH_REF) == ("github", _GH_REF)
    assert qualify_repo(*parse_repo_ref(_GH_REF)) == _GH_REF
    assert is_github(provider_of(_GH_REF))
    assert is_github(provider_of(None))


def test_azure_devops_qualifies_with_its_three_part_path():
    assert parse_repo_ref("azure_devops:org/proj/repo") == (
        "azure_devops",
        "org/proj/repo",
    )
    assert not is_github(provider_of("azure_devops:org/proj/repo"))


def test_a_clone_url_is_not_shredded_into_a_provider_called_https():
    url = "https://gitlab.example.com/platform/pipelines.git"
    assert parse_repo_ref(url) == ("github", url)


def test_an_unimplemented_host_is_not_a_qualification():
    assert parse_repo_ref("bitbucket:team/repo") == ("github", "bitbucket:team/repo")


def test_the_slug_gh_would_receive_never_carries_the_qualification():
    """`gh pr comment -R gitlab:group/project` is a resolution failure."""
    assert project_of(_GL_REF) == "platform/pipelines"
    assert ":" not in project_of(_GL_REF)


# ── the Triager's PR side-effect ─────────────────────────────────────────────


@pytest.fixture
def findings(tmp_path):
    d = tmp_path / "findings"
    d.mkdir()
    return d


def _side_effect(
    findings_dir: Path, source_meta: dict, report: str = "# Verdict\n\npass"
):
    from agents.triager import _run_pr_side_effect

    return _run_pr_side_effect(Path("/tmp"), findings_dir, source_meta, report)


@pytest.mark.parametrize("provider", ["gitlab", "azure_devops"])
def test_the_verdict_is_written_to_disk_rather_than_thrown_at_gh(provider, findings):
    """Off GitHub the gh-CLI comment is skipped, and the report survives.

    Written rather than dropped: the verification has already run and the report
    is the thing the tenant actually wanted.
    """
    result = _side_effect(
        findings,
        {"pr_number": 42, "repo_slug": "platform/pipelines", "provider": provider},
    )
    assert result["skipped"] is True
    assert result["provider"] == provider
    assert "not GitHub" in result["reason"]
    body = findings / "pr_comment_body.md"
    assert body.exists()
    assert "Verdict" in body.read_text()
    # No argv at all: refused before the subprocess, not after it failed.
    assert "argv" not in result


def test_a_github_tenant_still_gets_its_comment(findings, monkeypatch):
    """The refusal must not cost the case that always worked."""
    import agents.triager as triager

    monkeypatch.setattr(triager, "_pr_comment_dry_run", lambda: True)
    result = _side_effect(
        findings, {"pr_number": 42, "repo_slug": _GH_REF, "provider": "github"}
    )
    assert result["skipped"] is False
    assert result["argv"][:3] == ["gh", "pr", "comment"]
    assert "-R" in result["argv"]
    assert _GH_REF in result["argv"]


def test_a_spec_written_before_this_phase_reads_as_github(findings, monkeypatch):
    """No `provider` key on source.json means GitHub — no backfill needed."""
    import agents.triager as triager

    monkeypatch.setattr(triager, "_pr_comment_dry_run", lambda: True)
    result = _side_effect(findings, {"pr_number": 42, "repo_slug": _GH_REF})
    assert result["skipped"] is False
    assert result["argv"][:3] == ["gh", "pr", "comment"]


def test_no_pr_number_still_writes_the_body(findings):
    """The pre-existing fallback is untouched — the new one reuses it."""
    result = _side_effect(findings, {"provider": "github"})
    assert result["skipped"] is True
    assert (findings / "pr_comment_body.md").exists()


# ── what an ingest records ───────────────────────────────────────────────────


def test_an_ingest_records_the_declared_host_and_a_bare_slug(tmp_path):
    from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace

    result = create_spec_ingest_workspace(
        project_id="proj",
        spec_id="spec-1",
        spec_text="# Spec\n\n- AC#1: the widget ships\n",
        root=tmp_path,
        schedule=False,
        repo_ref=_GL_REF,
    )
    assert result
    source = json.loads(
        (
            tmp_path
            / "workspaces"
            / "proj"
            / "specs"
            / "spec-1"
            / "context"
            / "source.json"
        ).read_text()
    )
    assert source["provider"] == "gitlab"
    # BARE: this is what reaches `gh pr comment -R` on the GitHub path.
    assert source["repo_slug"] == "platform/pipelines"


def test_an_ingest_with_no_reference_reads_as_github(tmp_path):
    from agents.tools_pkg.tools.task_control import create_spec_ingest_workspace

    create_spec_ingest_workspace(
        project_id="proj",
        spec_id="spec-2",
        spec_text="# Spec\n\n- AC#1: the widget ships\n",
        root=tmp_path,
        schedule=False,
    )
    source = json.loads(
        (
            tmp_path
            / "workspaces"
            / "proj"
            / "specs"
            / "spec-2"
            / "context"
            / "source.json"
        ).read_text()
    )
    assert source["provider"] == "github"
    assert source["repo_slug"] is None

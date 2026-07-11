"""Tests for the agent-added dependency review + pinning gate (#650).

Fixture branches per the issue's step 5: clean pinned add, unpinned add,
known-CVE add (via a stubbed Trivy report on a pinned old version), and
no-manifest-change (signal skipped, zero behaviour change). Plus the Triager
envelope wiring: a gating fail downgrades a would-be success outcome to
human_review (never silently accept).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from agents.dependency_review import (  # noqa: E402
    read_dependency_review,
    review_dependencies,
    run_dependency_review,
)
from agents.triager import _build_completion_envelope  # noqa: E402

# ---------------------------------------------------------------------------
# fixture-repo plumbing
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
    )
    subprocess.run(  # noqa: S603 - fixed git argv in a test fixture
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
        check=True,
        capture_output=True,
        env=env,
    )


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _make_repo(tmp_path: Path, base_files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _write(repo, base_files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo


def _task_branch(repo: Path, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-b", "task")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "task change")


def _no_trivy(_: Path) -> tuple[list[dict[str, Any]], str | None]:
    return [], None


def _no_age(_: str, __: str) -> float | None:
    return None


def _review(repo: Path, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("base_ref", "main")
    kwargs.setdefault("trivy_fn", _no_trivy)
    kwargs.setdefault("age_fn", _no_age)
    return review_dependencies(repo, **kwargs)


# ---------------------------------------------------------------------------
# detection / skip paths
# ---------------------------------------------------------------------------


def test_no_manifest_change_is_skipped_without_scanning(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"app.py": "print('hi')\n"})

    calls: list[Path] = []

    def trivy(project_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
        calls.append(project_dir)
        return [], None

    block = _review(repo, trivy_fn=trivy)
    assert block["status"] == "skipped"
    assert block["gating"] is False
    assert block["reason"] == "no dependency manifest changes"
    assert calls == []  # zero added latency beyond the diff check


def test_not_a_git_repo_is_skipped(tmp_path: Path) -> None:
    block = review_dependencies(tmp_path, trivy_fn=_no_trivy, age_fn=_no_age)
    assert block["status"] == "skipped"
    assert block["gating"] is False


# ---------------------------------------------------------------------------
# pinning gate (2a)
# ---------------------------------------------------------------------------


def test_clean_pinned_add_passes(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\nhttpx>=0.27\n"})
    block = _review(repo)
    assert block["status"] == "pass"
    assert block["gating"] is False
    assert [p["name"] for p in block["packages"]] == ["httpx"]
    assert block["packages"][0]["pinned"] is True
    assert block["packages"][0]["change"] == "added"


def test_unpinned_python_add_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\nhttpx\n"})
    block = _review(repo)
    assert block["status"] == "fail"
    assert block["gating"] is True
    kinds = [(f["kind"], f["package"]) for f in block["findings"]]
    assert ("unpinned", "httpx") in kinds


def test_unpinned_npm_add_fails_and_range_passes(tmp_path: Path) -> None:
    base = json.dumps({"dependencies": {"react": "^18.2.0"}})
    repo = _make_repo(tmp_path, {"package.json": base})
    head = json.dumps({"dependencies": {"react": "^18.2.0", "zod-mini": "*"}})
    _task_branch(repo, {"package.json": head})
    block = _review(repo)
    assert block["status"] == "fail"
    assert any(f["kind"] == "unpinned" for f in block["findings"])

    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "task2")
    _write(
        repo,
        {
            "package.json": json.dumps(
                {"dependencies": {"react": "^18.2.0", "zod-mini": "^3.0.0"}}
            )
        },
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "pinned")
    assert _review(repo)["status"] == "pass"


def test_pyproject_added_dep_detected(tmp_path: Path) -> None:
    base = (
        '[project]\nname = "demo"\nversion = "0.1"\ndependencies = ["flask==3.0.0"]\n'
    )
    head = (
        '[project]\nname = "demo"\nversion = "0.1"\n'
        'dependencies = ["flask==3.0.0", "orjson"]\n'
    )
    repo = _make_repo(tmp_path, {"pyproject.toml": base})
    _task_branch(repo, {"pyproject.toml": head})
    block = _review(repo)
    assert block["status"] == "fail"
    assert block["findings"][0]["package"] == "orjson"


def test_unchanged_constraint_is_not_reported(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask\n"})
    _task_branch(repo, {"requirements.txt": "flask\nhttpx==0.27.0\n"})
    block = _review(repo)
    # flask was already unpinned at base — only the agent's own add is reviewed.
    assert [p["name"] for p in block["packages"]] == ["httpx"]
    assert block["status"] == "pass"


# ---------------------------------------------------------------------------
# known-vuln gate (2b)
# ---------------------------------------------------------------------------


def _vuln_trivy(
    rows: list[dict[str, Any]],
) -> Any:
    def trivy(_: Path) -> tuple[list[dict[str, Any]], str | None]:
        return rows, None

    return trivy


def test_known_cve_on_added_pinned_package_fails(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\npyyaml==5.3.1\n"})
    rows = [
        {
            "PkgName": "PyYAML",
            "VulnerabilityID": "CVE-2020-14343",
            "Severity": "CRITICAL",
            "InstalledVersion": "5.3.1",
        }
    ]
    block = _review(repo, trivy_fn=_vuln_trivy(rows))
    assert block["status"] == "fail"
    assert block["gating"] is True
    assert any(
        f["kind"] == "vulnerability" and f["package"] == "pyyaml"
        for f in block["findings"]
    )


def test_vuln_on_preexisting_package_does_not_gate(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\nhttpx==0.27.0\n"})
    rows = [
        {
            "PkgName": "flask",
            "VulnerabilityID": "CVE-0000-0001",
            "Severity": "HIGH",
            "InstalledVersion": "3.0.0",
        }
    ]
    block = _review(repo, trivy_fn=_vuln_trivy(rows))
    assert block["status"] == "pass"


def test_trivy_unavailable_is_honest_advisory_not_silent_pass(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\nhttpx==0.27.0\n"})

    def trivy(_: Path) -> tuple[None, str]:
        return None, "trivy binary not available; known-vuln check not_run"

    block = _review(repo, trivy_fn=trivy)
    assert block["status"] == "advisory"
    assert block["gating"] is False
    assert any(f["kind"] == "scan_unavailable" for f in block["findings"])


# ---------------------------------------------------------------------------
# sanity heuristics (2c) — advisory only
# ---------------------------------------------------------------------------


def test_typosquat_and_age_are_advisory_not_gating(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"requirements.txt": "flask==3.0.0\nreqeusts==1.0\n"})
    block = _review(repo, age_fn=lambda _n, _e: 3.0)
    assert block["status"] == "advisory"
    assert block["gating"] is False
    kinds = {f["kind"] for f in block["findings"]}
    assert {"typosquat", "new_package_age"} <= kinds


# ---------------------------------------------------------------------------
# persistence + envelope wiring (steps 3-4)
# ---------------------------------------------------------------------------


def test_run_dependency_review_persists_findings(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"requirements.txt": "flask==3.0.0\n"})
    _task_branch(repo, {"app.py": "x = 1\n"})
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    block = run_dependency_review(spec_dir, repo)
    assert block is not None
    persisted = read_dependency_review(spec_dir)
    assert persisted == block


def _success_status() -> dict[str, Any]:
    return {
        "task_id": "042",
        "status": "triaged",
        "verdicts_count": 3,
        "committed_count": 2,
        "flagged_count": 1,
    }


def _write_dep_block(spec_dir: Path, status: str, reason: str = "r") -> None:
    findings = spec_dir / "findings"
    findings.mkdir(parents=True, exist_ok=True)
    (findings / "dependency_review.json").write_text(
        json.dumps(
            {
                "status": status,
                "gating": status == "fail",
                "reason": reason,
                "base_ref": "origin/main",
                "manifests": ["requirements.txt"],
                "packages": [],
                "findings": [],
            }
        )
    )


def test_envelope_gates_success_to_human_review_on_fail(tmp_path: Path) -> None:
    _write_dep_block(tmp_path, "fail", "unpinned dependency 'httpx' added")
    env = _build_completion_envelope(tmp_path, _success_status())
    assert env["dependency_review"]["status"] == "fail"
    assert env["outcome"] == "human_review"
    assert "dependency_review" in env["halt_reason"]


def test_envelope_carries_block_without_gating_on_pass(tmp_path: Path) -> None:
    _write_dep_block(tmp_path, "pass")
    env = _build_completion_envelope(tmp_path, _success_status())
    assert env["dependency_review"]["status"] == "pass"
    assert env["outcome"] == "success"
    assert "halt_reason" not in env


def test_envelope_unchanged_when_review_never_ran(tmp_path: Path) -> None:
    env = _build_completion_envelope(tmp_path, _success_status())
    assert "dependency_review" not in env
    assert env["outcome"] == "success"


def test_envelope_fail_does_not_mask_a_real_failure(tmp_path: Path) -> None:
    _write_dep_block(tmp_path, "fail")
    env = _build_completion_envelope(
        tmp_path, {"task_id": "042", "status": "triager_failed"}
    )
    # An already-failed run stays a failure; the gate only guards accepts.
    assert env["outcome"] == "failure"
    assert env["dependency_review"]["status"] == "fail"

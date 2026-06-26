"""Turn portal test runs into GitHub issues — the testing flow.

For each portal report, open (or update) a **tracking issue** labeled
``portal-test`` summarising the run (coverage + findings + evidence pointer).
This is the seam TFactory uses to file verification findings: a run becomes a
durable, assignable, trackable artifact rather than a console scroll.

    python -m harness.github_flow <repo> [portal ...]      # default: all 4

Uses the ``gh`` CLI (already authenticated). Idempotent: a portal's tracking
issue is reused (commented) on re-runs rather than duplicated.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "portal-test"


def _gh(args: list[str], check: bool = True) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {out.stderr[:200]}")
    return out.stdout.strip()


def _ensure_label(repo: str) -> None:
    _gh(
        [
            "label",
            "create",
            LABEL,
            "--repo",
            repo,
            "--color",
            "5319e7",
            "--description",
            "Automated Factory-portal UI test",
            "--force",
        ],
        check=False,
    )


def _summary(report: Path) -> tuple[str, int]:
    text = report.read_text()
    cov = re.search(r"\| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|", text)
    findings = int(cov.group(5)) if cov else 0
    head = text.split("## Walkthrough")[0]
    return head, findings


def file_for_portal(repo: str, key: str) -> None:
    portal = config.PORTALS[key]
    report = Path(config.REPORTS_DIR) / key / "report.md"
    if not report.exists():
        print(f"[{key}] no report — skip")
        return
    body, findings = _summary(report)
    body += (
        f"\n---\nEvidence: `reports/{key}/screenshots/` + `reports/{key}/video/{key}.webm` in this repo.\n"
        f"Re-run: `nix develop --command python -m harness.run {key}`\n"
    )
    title = f"Portal test: {portal.name}"
    # Find an existing tracking issue (idempotent).
    existing = _gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            LABEL,
            "--state",
            "open",
            "--search",
            title,
            "--json",
            "number,title",
            "--jq",
            f'.[] | select(.title=="{title}") | .number',
        ],
        check=False,
    )
    if existing.strip():
        num = existing.strip().splitlines()[0]
        _gh(["issue", "comment", num, "--repo", repo, "--body", f"Re-run.\n\n{body}"])
        print(f"[{key}] updated tracking issue #{num}")
    else:
        url = _gh(
            [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                title,
                "--label",
                LABEL,
                "--body",
                body,
            ]
        )
        print(f"[{key}] opened tracking issue {url}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit(
            "usage: python -m harness.github_flow <owner/repo> [portal ...]"
        )
    repo = argv[1]
    keys = argv[2:] or list(config.PORTALS)
    _ensure_label(repo)
    for k in keys:
        file_for_portal(repo, k)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

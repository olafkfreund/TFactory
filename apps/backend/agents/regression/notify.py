"""Tell AIFactory a regression run failed on a commit it merged (AIFactory#2059).

AIFactory's ``POST /api/tasks/{task_id}/post-merge-regression`` opens a revert PR
(never merged). Best effort: any failure is logged and never fails the run.
Inert unless ``TFACTORY_PUBLIC_URL`` is https, the spec carries an AIFactory
envelope, and the checkout under test is at the reported commit.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .diff import RegressionDiff

logger = logging.getLogger(__name__)

Sender = Callable[[str, dict], None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.HTTPError(
            args[0].full_url, args[3], "redirect refused", args[4], None
        )


def _post(url: str, body: dict) -> None:
    from agents.handback.send import _auth_headers  # noqa: PLC0415 - heavy imports
    from tools.runners.net_guard import assert_safe_target_url  # noqa: PLC0415

    assert_safe_target_url(url, allow_private=True, allow_loopback=True)
    req = urllib.request.Request(  # noqa: S310 - checked above
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **_auth_headers(url)},
    )
    # No redirects: urllib would resend the bearer token to another origin.
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=30):
        pass


def _head(repo_root: Path) -> str:
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return out.stdout.strip()


def notify_aifactory(
    *,
    spec_dir: Path | None,
    repo_root: Path,
    project_id: str,
    commit: str | None,
    run_id: str,
    diff: RegressionDiff,
    sender: Sender | None = None,
) -> bool:
    """POST the failing run to AIFactory; True when a request was sent."""
    public = (os.environ.get("TFACTORY_PUBLIC_URL") or "").rstrip("/")
    if not (
        diff.has_regressions and commit and spec_dir and public.startswith("https://")
    ):
        return False
    try:
        env = json.loads((Path(spec_dir) / "context" / "source.json").read_text())[
            "aifactory"
        ]
        task_id, api_url = env["task_id"], env["api_url"].rstrip("/")
        if not _head(Path(repo_root)).startswith(commit.lower()):
            logger.warning("regression notify skipped: checkout is not at %s", commit)
            return False
        (sender or _post)(
            f"{api_url}/api/tasks/{task_id}/post-merge-regression",
            {
                "merge_sha": commit,
                "run_url": f"{public}/api/projects/{project_id}/regression?run={run_id}",
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001 - advisory call must never fail the run
        logger.warning("regression notify to AIFactory failed: %s", exc)
        return False

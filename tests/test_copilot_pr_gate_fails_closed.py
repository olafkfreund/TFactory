"""A gate whose API call fails must go red, not warn and pass.

TFactory#1148. `.github/workflows/copilot-pr-test.yml` POSTs to
`${TFACTORY_URL}/api/tasks/from-pr` -- a route no router in this repo declares
-- and its trigger step used to swallow the status:

    if [ "$HTTP_STATUS" -lt 200 ] || [ "$HTTP_STATUS" -ge 300 ]; then
      echo "task_created=false" >> "$GITHUB_OUTPUT"
      echo "::warning::TFactory API returned HTTP $HTTP_STATUS; suite not started"

A 404 therefore left the job GREEN: a verification gate reporting success
having verified nothing (Factory#832).

Behavioural, not structural. The step's script is now pure bash reading its
inputs from `env:` -- no `${{ }}` inside the shell body -- so the test can lift
it straight out of the YAML and run it against a stub server. It pins the one
property that matters: a non-2xx, or a server that is not there at all, exits
non-zero.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github/workflows/copilot-pr-test.yml"
)


def _trigger_script() -> str:
    """The `run:` body of the step that calls the TFactory API."""
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    steps = workflow["jobs"]["tfactory-test"]["steps"]
    trigger = [s for s in steps if s.get("id") == "trigger"]
    assert len(trigger) == 1, "expected exactly one step with id: trigger"
    script = trigger[0]["run"]
    assert "${{" not in script, (
        "the trigger script must take its inputs from env:, not from ${{ }} "
        "interpolation -- otherwise it cannot be run or tested outside Actions"
    )
    return script


def _serve(status: int, body: bytes) -> http.server.HTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(url: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "TFACTORY_URL": url,
            "TFACTORY_TOKEN": "stub-token",
            "PR_NUMBER": "1",
            "REPO": "olafkfreund/TFactory",
            "HEAD_SHA": "0" * 40,
            "GITHUB_OUTPUT": str(Path(tmp) / "output"),
        }
        return subprocess.run(
            ["bash", "-c", _trigger_script()],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )


@pytest.mark.parametrize("status", [404, 401, 500])
def test_non_2xx_fails_the_step(status: int) -> None:
    server = _serve(status, b'{"detail":"Not Found"}')
    try:
        result = _run(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.returncode != 0, (
        f"HTTP {status} must fail the gate, not warn: {result.stdout}{result.stderr}"
    )
    assert "::warning" not in result.stdout, (
        "a suite that never started is not a warning"
    )
    assert "::error" in result.stdout


def test_unreachable_api_fails_the_step() -> None:
    # Port 1 on loopback: nothing listens, so curl itself errors.
    result = _run("http://127.0.0.1:1")
    assert result.returncode != 0, f"{result.stdout}{result.stderr}"


def test_2xx_passes_and_reports_the_task_id() -> None:
    server = _serve(201, b'{"task_id":"spec-042"}')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = {
                **os.environ,
                "TFACTORY_URL": f"http://127.0.0.1:{server.server_port}",
                "TFACTORY_TOKEN": "stub-token",
                "PR_NUMBER": "1",
                "REPO": "olafkfreund/TFactory",
                "HEAD_SHA": "0" * 40,
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(
                ["bash", "-c", _trigger_script()],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == 0, f"{result.stdout}{result.stderr}"
            assert "task_id=spec-042" in output.read_text()
    finally:
        server.shutdown()

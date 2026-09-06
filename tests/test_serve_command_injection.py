"""Command-injection regression for the api/browser-lane serve command
(TFactory#1290).

The data flow: an imported GitHub issue body → a PFactory plan → the task
contract's ``environment.serve_command`` → ``nix_env.detect_serve_command``
(which returned it verbatim) → ``LocalServeRuntime.start`` on the VERIFY HOST.
That last hop used ``subprocess`` with ``shell=True``, carrying a comment
asserting the command was "not untrusted" — an invariant the flow never held.

These tests spawn REAL processes with REAL payloads and assert the injected
half did not execute (a marker file is never created), rather than asserting
that some string was escaped. They fail on the pre-fix code.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest
from agents.nix_env import detect_serve_command
from tools.runners.local_serve_runtime import LocalServeRuntime, LocalServeRuntimeError

# The shell metacharacter surface, not just the `&`/`|` of the original report.
# Each template gets `{py}` (a real interpreter) and `{marker}` (a path that
# must NOT come into existence). Under `shell=True` every one of these creates
# the marker; under an exec'd argv every one is inert.
_PAYLOADS = {
    "semicolon": '{py} -c "import time; time.sleep(0.05)" ; {py} -c "open({marker},\'w\')"',
    "ampersand": '{py} -c "import time; time.sleep(0.05)" & {py} -c "open({marker},\'w\')"',
    "pipe": '{py} -c "print(1)" | {py} -c "open({marker},\'w\')"',
    "and_and": '{py} -c "print(1)" && {py} -c "open({marker},\'w\')"',
    "subshell": '{py} -c "print(1)" $({py} -c "open({marker},\'w\')")',
    "backtick": '{py} -c "print(1)" `{py} -c "open({marker},\'w\')"`',
    "newline": '{py} -c "print(1)"\n{py} -c "open({marker},\'w\')"',
    "redirect": '{py} -c "print(1)" > {marker_raw}',
}


def _payload(template: str, marker: Path) -> str:
    return template.format(
        py=shlex.quote(sys.executable),
        marker=shlex.quote(repr(str(marker))),
        marker_raw=shlex.quote(str(marker)),
    )


# ── the host sink: LocalServeRuntime must not run a shell ─────────────────


@pytest.mark.parametrize("name", sorted(_PAYLOADS))
def test_start_never_executes_an_injected_command(name, tmp_path):
    """THE regression test. Spawns for real; asserts the second command in the
    payload never ran. Mutation check: restore ``shell=True`` in
    ``LocalServeRuntime.start`` and every case here creates the marker."""
    marker = tmp_path / f"pwned-{name}"
    rt = LocalServeRuntime(_payload(_PAYLOADS[name], marker), tmp_path, 8123)
    try:
        rt.start()
    except (LocalServeRuntimeError, OSError):
        # Refusing to launch at all is also a pass: nothing executed.
        assert not marker.exists()
        return
    proc = rt._proc
    assert proc is not None
    proc.wait(timeout=30)  # the shell would have created the marker by now
    rt.stop()
    assert not marker.exists(), (
        f"injected command executed on the verify host via {name!r} payload"
    )


def test_start_execs_an_argv_list_with_no_shell():
    calls = {}

    class _Proc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return _Proc()

    rt = LocalServeRuntime(
        "python -m uvicorn app:app --port 8123",
        Path("/proj"),
        8123,
        popen_fn=fake_popen,
    )
    rt.start()
    assert calls["cmd"] == ["python", "-m", "uvicorn", "app:app", "--port", "8123"]
    assert "shell" not in calls["kwargs"]  # no shell, not even shell=False


def test_start_rejects_an_unrunnable_command():
    rt = LocalServeRuntime("   ", Path("/tmp"), 8123, popen_fn=lambda *a, **k: None)
    with pytest.raises(LocalServeRuntimeError, match="empty or unparseable"):
        rt.start()


# ── the source: a metacharacter-bearing contract value is refused ─────────


@pytest.mark.parametrize("name", sorted(_PAYLOADS))
def test_detect_serve_command_refuses_an_injected_contract_value(name, tmp_path):
    """``environment.serve_command`` is spec-derived, so it is validated before
    being returned — this also protects the in-Job shell preludes built by
    ``build_nix_job_command``/``build_browser_job_command``."""
    env = {
        "serve_command": _PAYLOADS[name].format(
            py="uvicorn", marker="'/tmp/x'", marker_raw="/tmp/x"
        )
    }
    # empty checkout → nothing detectable → None is the honest answer
    assert detect_serve_command(tmp_path, env) is None


def test_detect_serve_command_still_honours_a_plain_contract_value(tmp_path):
    env = {"serve_command": "python -m uvicorn app:app --host 127.0.0.1 --port 9001"}
    assert (
        detect_serve_command(tmp_path, env)
        == "python -m uvicorn app:app --host 127.0.0.1 --port 9001"
    )

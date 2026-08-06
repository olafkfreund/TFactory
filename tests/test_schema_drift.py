"""Tests for the cross-repo Task Contract schema-drift guard (#679, #940).

Ported from PFactory's tests/test_schema_drift.py (PFactory#440, PR #442) —
TFactory carries its own copy of the gate script and had no tests for it.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "check_schema_drift.py"
_VENDORED = _ROOT / "apps/backend/contracts/task-contract-v2.schema.json"

# Load the standalone script as a module.
_spec = importlib.util.spec_from_file_location("check_schema_drift", _SCRIPT)
csd = importlib.util.module_from_spec(_spec)
sys.modules["check_schema_drift"] = csd
_spec.loader.exec_module(csd)


# -- check_drift: directional subset (canonical is a subset of vendored) -------


def test_no_drift_when_vendored_superset():
    canon = {"properties": {"a": {"type": "string"}}}
    vend = {"properties": {"a": {"type": "string"}, "extra": {}}}  # vendored may add
    assert csd.check_drift(canon, vend) == []


def test_missing_key_is_drift():
    canon = {"properties": {"change_mode": {"enum": ["migration"]}}}
    vend = {"properties": {}}
    problems = csd.check_drift(canon, vend)
    assert any("change_mode" in p for p in problems)


def test_enum_narrowing_is_drift():
    canon = {"lanes": ["unit", "equivalence"]}
    vend = {"lanes": ["unit"]}  # vendored missing a canonical value
    problems = csd.check_drift(canon, vend)
    assert any("equivalence" in p for p in problems)


def test_descriptions_ignored():
    canon = {"properties": {"a": {"description": "canonical text", "type": "string"}}}
    vend = {"properties": {"a": {"description": "different", "type": "string"}}}
    assert csd.check_drift(canon, vend) == []


def test_scalar_mismatch_is_drift():
    assert csd.check_drift({"type": "string"}, {"type": "integer"})


# -- a gate that cannot run must fail, never pass (#940, Factory#433) ----------


def _self_signed_https_server(tmp_path: Path) -> http.server.HTTPServer:
    """A real local HTTPS server whose cert no client will trust."""
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    openssl = shutil.which("openssl")
    subprocess.run(  # noqa: S603 - fixed argv, resolved binary, test-only
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(crt), "-days", "1",
            "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # don't offer TLSv1/1.1
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _http_server(directory: Path) -> http.server.HTTPServer:
    """A real local plain-HTTP server: the success direction of the same code path."""

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a):  # keep pytest output clean
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_certificate_failure_fails_the_gate(tmp_path):
    """A TLS cert failure is deterministic: exit 1, never a silent green skip.

    Driven through a real ``urlopen`` against a real HTTPS server because the
    whole bug was the wrapping: the cert error arrives as
    ``URLError(reason=SSLCertVerificationError)`` and is NOT an ``ssl.SSLError``,
    so a mock of ``urlopen`` would encode the same wrong assumption and pass
    while the real thing still failed.
    """
    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    srv = _self_signed_https_server(tmp_path)
    try:
        rc = csd.main(["--canonical", f"https://localhost:{srv.server_address[1]}/x.json"])
    finally:
        srv.shutdown()
    assert rc == 1


def test_is_transient_unwraps_urlopen_reason():
    cert_err = ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
    assert not csd.is_transient(urllib.error.URLError(cert_err))
    assert not csd.is_transient(cert_err)
    # A 404 on the pinned ref is deterministic too - reason is a str, not a cause.
    assert not csd.is_transient(urllib.error.HTTPError("u", 404, "Not Found", {}, None))
    # Genuinely transient causes may still soft-skip.
    assert csd.is_transient(urllib.error.URLError(TimeoutError("timed out")))
    assert csd.is_transient(urllib.error.URLError(socket.gaierror(-2, "no host")))


def test_soft_skip_is_loud(capsys):
    """A skip must be visibly distinguishable from a pass."""
    csd._warn_skipped(urllib.error.URLError(TimeoutError("timed out")))
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "NOT VERIFIED" in out
    assert "::warning" in out  # GitHub annotation, visible on the checks page


# -- the control direction: a fix that hard-fails on everything is not a fix ---


def test_transient_failure_still_soft_skips_loudly(capsys):
    """A real transient failure (DNS) still exits 0 - but says so at volume.

    ``.invalid`` is reserved by RFC 2606 and never resolves, so this drives a
    real ``urlopen`` to a real ``URLError(reason=socket.gaierror)``.
    """
    rc = csd.main(["--canonical", "https://tfactory-940.invalid/x.json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIPPED" in out and "NOT VERIFIED" in out and "::warning" in out


def test_successful_fetch_still_runs_the_comparison(tmp_path, capsys):
    """A fetch that works must still compare - in both verdicts."""
    vendored = json.loads(_VENDORED.read_text(encoding="utf-8"))
    (tmp_path / "same.json").write_text(json.dumps(vendored), encoding="utf-8")
    (tmp_path / "drifted.json").write_text(
        json.dumps({**vendored, "__canonical_only__": {"type": "string"}}),
        encoding="utf-8",
    )
    srv = _http_server(tmp_path)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert csd.main(["--canonical", f"{base}/same.json"]) == 0
        assert "in sync" in capsys.readouterr().out
        assert csd.main(["--canonical", f"{base}/drifted.json"]) == 1
        assert "__canonical_only__" in capsys.readouterr().out
    finally:
        srv.shutdown()


# -- the live vendored schema is in sync with the canonical hub copy -----------


def test_vendored_in_sync_with_local_hub():
    """When the Factory hub checkout is present, the vendored copy must match."""
    hub = _ROOT.parent / "Factory" / "apis" / "task-contract.schema.json"
    if not hub.is_file():
        pytest.skip("Factory hub checkout not available")
    canonical = json.loads(hub.read_text(encoding="utf-8"))
    vendored = json.loads(_VENDORED.read_text(encoding="utf-8"))
    assert csd.check_drift(canonical, vendored) == []

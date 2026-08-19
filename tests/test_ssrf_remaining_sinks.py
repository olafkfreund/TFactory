"""The three spec-derived outbound sinks #1117 left unguarded, driven end to end.

Every test here calls the REAL entry point -- ``health_gate.gate``,
``DockerRunRuntime.wait_for_healthy``, ``handback.send.default_sender`` -- and
never ``assert_safe_target_url`` directly. Testing the guard function proves the
guard works; it does not prove any of these three call sites reaches it, which
is the entire content of #1117. Delete any one of the three
``assert_safe_target_url`` calls and a test in this module must go red.

Address choices are deliberate:

* ``169.254.169.254`` -- the cloud instance-credentials endpoint. Refused in
  every posture, which is the property worth a blocking test.
* ``10.0.0.5`` -- RFC-1918, refused where ``allow_private`` is not set and
  accepted where it is.
* ``8.8.8.8`` -- a literal address with ``is_global`` True. NOT ``203.0.113.x``:
  Python's ``ipaddress`` reports TEST-NET-3 as private, so a "public URL still
  works" case built on it would pass only because ``allow_private=True`` and
  would prove nothing. Being a literal, it needs no DNS either, so these tests
  stay hermetic.
"""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

import pytest
from agents.handback.send import default_sender
from agents.health_gate import gate, probe
from tools.runners.docker_run_runtime import DockerRunRuntime, DockerRunRuntimeError

METADATA = "http://169.254.169.254"
PRIVATE = "http://10.0.0.5"
PUBLIC = "http://8.8.8.8"


def test_the_public_fixture_is_actually_public() -> None:
    """Guards the guard tests: if PUBLIC stopped being global they'd go vacuous."""
    assert ipaddress.ip_address("8.8.8.8").is_global
    assert not ipaddress.ip_address("10.0.0.5").is_global


# ── health_gate.gate -- .tfactory.yml target health probe ────────────────────


def _exploded(url, timeout):  # noqa: ANN001, ARG001 - opener seam signature
    raise AssertionError(f"guard let the fetch through for {url}")


def test_gate_refuses_the_metadata_range() -> None:
    r = gate(METADATA, {"path": "/healthz"}, opener=_exploded)
    assert not r.ok
    assert "blocked_unsafe_target" in r.detail


def test_gate_refuses_a_private_address() -> None:
    """No ``allow_private`` here, matching AppRuntime's default posture."""
    r = gate(PRIVATE, {"path": "/healthz"}, opener=_exploded)
    assert not r.ok
    assert "blocked_unsafe_target" in r.detail


def test_gate_still_probes_a_public_target() -> None:
    r = gate(PUBLIC, {"path": "/healthz"}, opener=lambda u, t: 200)
    assert r.ok
    assert r.url == "http://8.8.8.8/healthz"


def test_probe_allows_loopback_because_a_self_served_sut_lives_there() -> None:
    r = probe("http://127.0.0.1:8000/healthz", opener=lambda u, t: 200)
    assert r.ok


# ── DockerRunRuntime.wait_for_healthy -- .tfactory.yml wait_for.url ──────────


def _docker_target(url: str):
    return SimpleNamespace(
        name="sut",
        image="img:latest",
        ports=[],
        env={},
        command=None,
        wait_for=[SimpleNamespace(url=url, expect_status=200, timeout_seconds=1)],
    )


@pytest.mark.parametrize("bad", [METADATA, PRIVATE])
def test_docker_run_health_poll_refuses_unsafe_targets(bad: str) -> None:
    rt = DockerRunRuntime(_docker_target(bad), clock=lambda: 0.0)
    with pytest.raises(DockerRunRuntimeError, match="blocked_unsafe_target"):
        rt.wait_for_healthy()


def test_docker_run_health_poll_still_reaches_a_public_target(monkeypatch) -> None:
    seen: list[str] = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):  # noqa: ANN001, ARG001
        seen.append(req.full_url)
        return _Resp()

    monkeypatch.setattr("tools.runners.docker_run_runtime.urlrequest.urlopen", _urlopen)
    rt = DockerRunRuntime(_docker_target(f"{PUBLIC}/healthz"), clock=lambda: 0.0)
    rt.wait_for_healthy()
    assert seen == [f"{PUBLIC}/healthz"]


def test_docker_run_guard_checks_the_rewritten_url_not_the_declared_one() -> None:
    """The port rewrite runs before the check, so both see the same string.

    ``_poll_one`` binds ``url = self._rewrite_url(...)`` once and then guards
    and fetches that variable. Checking ``wait_for.url`` while fetching the
    rewritten one would be the classic validated-one-string-sent-another bug.
    """
    rt = DockerRunRuntime(_docker_target(f"{PUBLIC}:9000/healthz"), clock=lambda: 0.0)
    rt.host_ports = {"9000": "34567"}
    assert rt.target_url == "http://8.8.8.8:34567/healthz"


# ── handback default_sender -- source.json aifactory.api_url ────────────────


def test_handback_refuses_to_post_at_the_metadata_endpoint() -> None:
    from tools.runners.net_guard import UnsafeTargetURLError

    with pytest.raises(UnsafeTargetURLError):
        default_sender({"api_url": METADATA, "task_id": "p:s", "fix_request_md": "x"})


def test_handback_allows_loopback_and_private_because_that_is_the_default(
    monkeypatch,
) -> None:
    """``TFACTORY_AIFACTORY_API_URL`` defaults to localhost:3101 and AIFactory
    is commonly cluster-internal, so both postures are open here by design."""
    posted: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def _urlopen(req, timeout=None):  # noqa: ANN001, ARG001
        posted.append(req.full_url)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    for base in ("http://127.0.0.1:3101", PRIVATE):
        out = default_sender({"api_url": base, "task_id": "p:s", "fix_request_md": "x"})
        assert out == {"ok": True}
    assert posted == [
        "http://127.0.0.1:3101/api/tasks/p:s/apply-correction",
        "http://10.0.0.5/api/tasks/p:s/apply-correction",
    ]

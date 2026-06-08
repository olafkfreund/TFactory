"""Tests for the pre-lane health gate + target-URL resolution (#234, epic #232).

Pure — the HTTP probe and kubectl call are injected via seams, so no network or
cluster is touched.
"""

from __future__ import annotations

import pytest
from agents.health_gate import (
    discover_ingress_url,
    gate,
    probe,
    resolve_target_url,
)

# ─── probe ───────────────────────────────────────────────────────────────


def test_probe_ok():
    r = probe("http://x/healthz", opener=lambda u, t: 200)
    assert r.ok and r.status_code == 200 and r.detail == ""


def test_probe_wrong_status():
    r = probe("http://x/healthz", expect_status=200, opener=lambda u, t: 503)
    assert not r.ok
    assert "got 503" in r.detail


def test_probe_unreachable():
    def boom(u, t):
        raise ConnectionError("refused")

    r = probe("http://x/healthz", opener=boom)
    assert not r.ok
    assert "unreachable" in r.detail


# ─── gate ────────────────────────────────────────────────────────────────


def test_gate_passes_through_without_config():
    assert gate("http://x", None).ok is True
    assert gate(None, {"path": "/healthz"}).ok is True


def test_gate_builds_url_and_probes():
    seen = {}

    def opener(u, t):
        seen["url"] = u
        return 200

    r = gate("https://app.example.com/", {"path": "/healthz", "expect_status": 200}, opener=opener)
    assert r.ok
    assert seen["url"] == "https://app.example.com/healthz"


def test_gate_fails_on_bad_status():
    r = gate("https://app.example.com", {"path": "/up", "expect_status": 200}, opener=lambda u, t: 500)
    assert not r.ok
    assert r.url == "https://app.example.com/up"


# ─── resolve_target_url ──────────────────────────────────────────────────


def test_resolve_prefers_env_override():
    url = resolve_target_url({"base_url": "https://declared/"}, env={"TFACTORY_TARGET_URL": "https://ci-deployed/"})
    assert url == "https://ci-deployed"


def test_resolve_uses_base_url():
    assert resolve_target_url({"base_url": "https://declared/"}, env={}) == "https://declared"


def test_resolve_none_when_absent():
    assert resolve_target_url({}, env={}) is None
    assert resolve_target_url(None, env={}) is None


# ─── discover_ingress_url ────────────────────────────────────────────────


def test_discover_ingress_returns_url():
    url = discover_ingress_url("prod", "web", runner=lambda args: "app.example.com")
    assert url == "https://app.example.com"


def test_discover_ingress_custom_scheme():
    url = discover_ingress_url("prod", "web", scheme="http", runner=lambda args: "internal.svc")
    assert url == "http://internal.svc"


def test_discover_ingress_empty_host_none():
    assert discover_ingress_url("prod", "web", runner=lambda args: "") is None


def test_discover_ingress_kubectl_failure_none():
    def boom(args):
        raise RuntimeError("no cluster")

    assert discover_ingress_url("prod", "web", runner=boom) is None


def test_discover_ingress_passes_expected_args():
    seen = {}

    def runner(args):
        seen["args"] = args
        return "h"

    discover_ingress_url("ns1", "svc1", runner=runner)
    assert "kubectl" in seen["args"]
    assert "ns1" in seen["args"] and "svc1" in seen["args"]

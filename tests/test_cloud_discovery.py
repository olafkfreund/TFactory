"""Tests for the cloud discovery primitive (#133/#135).

Backend-pure: the provider CLI runner is injected with canned JSON — no real
cloud, no network, no mutations.
"""

from __future__ import annotations

import json

import pytest
from agents.cloud.discovery import (
    AccessResult,
    CloudDiscoveryError,
    access_check,
    discover,
)


def _cmd(returncode: int, stdout: str):
    return type("C", (), {"returncode": returncode, "stdout": stdout})()


def _aws_runner(*, identity_ok=True, fail=None):
    """Return a runner that answers AWS read-only calls from canned data.

    ``fail`` is a set of argv-substrings whose calls should return rc=1.
    """
    fail = fail or set()

    def run(argv):
        joined = " ".join(argv)
        if any(f in joined for f in fail):
            return _cmd(1, "")
        if "get-caller-identity" in joined:
            if not identity_ok:
                return _cmd(1, "")
            return _cmd(0, json.dumps({
                "Account": "533267307120",
                "Arn": "arn:aws:iam::533267307120:user/Olaf.Freund",
            }))
        if "list-buckets" in joined:
            return _cmd(0, json.dumps([{"Name": f"b{i}"} for i in range(12)]))
        if "get-account-summary" in joined:
            return _cmd(0, json.dumps({"SummaryMap": {"Users": 18, "Roles": 121, "Policies": 113}}))
        if "describe-vpcs" in joined:
            return _cmd(0, json.dumps({"Vpcs": [{}, {}, {}]}))
        if "describe-instances" in joined:
            return _cmd(0, json.dumps({"Reservations": [{}, {}, {}]}))
        if "list-functions" in joined:
            return _cmd(0, json.dumps({"Functions": [{}] * 10}))
        return _cmd(0, "{}")

    return run


# ── access_check ─────────────────────────────────────────────────────────────


def test_access_check_aws_success() -> None:
    r = access_check("aws", profile="Calitii", runner=_aws_runner())
    assert isinstance(r, AccessResult)
    assert r.ok is True
    assert r.account == "533267307120"
    assert r.identity == "Olaf.Freund"


def test_access_check_aws_failure() -> None:
    r = access_check("aws", runner=_aws_runner(identity_ok=False))
    assert r.ok is False
    assert r.error


def test_access_check_passes_profile() -> None:
    seen = {}

    def run(argv):
        seen["argv"] = argv
        return _cmd(0, json.dumps({"Account": "1", "Arn": "arn:aws:iam::1:user/x"}))

    access_check("aws", profile="Calitii", runner=run)
    assert "--profile" in seen["argv"] and "Calitii" in seen["argv"]


def test_access_check_unsupported_provider_raises() -> None:
    with pytest.raises(CloudDiscoveryError):
        access_check("digitalocean")


def test_access_check_unimplemented_provider_raises() -> None:
    with pytest.raises(CloudDiscoveryError, match="not implemented"):
        access_check("gcp")


def test_assumed_role_arn_name() -> None:
    def run(argv):
        return _cmd(0, json.dumps({
            "Account": "1",
            "Arn": "arn:aws:sts::1:assumed-role/AdminRole/session-123",
        }))

    r = access_check("aws", runner=run)
    assert r.identity == "AdminRole"


# ── discover ─────────────────────────────────────────────────────────────────


def test_discover_builds_inventory() -> None:
    inv = discover(
        "aws", profile="Calitii", regions=["us-east-1", "eu-west-2"], runner=_aws_runner()
    )
    assert inv["provider"] == "aws"
    assert inv["account"] == "533267307120"
    assert inv["identity"] == "Olaf.Freund"
    assert inv["global"]["s3"]["count"] == 12
    assert inv["global"]["iam"] == {"users": 18, "roles": 121, "policies": 113}
    assert inv["regions"]["us-east-1"] == {"vpcs": 3, "instances": 3, "lambdas": 10}
    assert inv["regions"]["eu-west-2"]["vpcs"] == 3


def test_discover_shape_feeds_render_cloud_topology() -> None:
    from agents.diagrams import render_cloud_topology

    inv = discover("aws", regions=["us-east-1"], runner=_aws_runner())
    out = render_cloud_topology(inv)
    assert out.startswith("graph LR\n")
    assert "AWS Account 533267307120" in out
    assert "📍 us-east-1" in out


def test_discover_services_filter_restricts_global_calls() -> None:
    inv = discover("aws", services=["s3"], runner=_aws_runner())
    assert "s3" in inv["global"]
    assert "iam" not in inv["global"]  # iam filtered out


def test_discover_access_failure_returns_error_inventory() -> None:
    inv = discover("aws", regions=["us-east-1"], runner=_aws_runner(identity_ok=False))
    assert inv["error"]
    assert inv["regions"] == {}  # no enumeration without access


def test_discover_tolerates_denied_service_calls() -> None:
    # iam denied → that section simply omitted, the rest still populates.
    inv = discover("aws", runner=_aws_runner(fail={"get-account-summary"}))
    assert "s3" in inv["global"]
    assert "iam" not in inv["global"]


def test_discover_unimplemented_provider_raises() -> None:
    with pytest.raises(CloudDiscoveryError, match="not implemented"):
        discover("azure")

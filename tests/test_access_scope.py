"""RFC-0007 (#87): map the contract access block to TFactory's runtime needs."""

from __future__ import annotations

from agents.access_scope import map_access_for_tfactory, val3_blocked


def test_empty_or_missing_block():
    for block in (None, {}, {"requirements": []}):
        m = map_access_for_tfactory(block)
        assert m == {
            "needs_egress": False,
            "credential_refs": [],
            "ready": [],
            "blocked": [],
        }
        assert val3_blocked(m) is False


def test_curated_resources_are_ready_and_drive_egress_and_refs():
    block = {
        "requirements": [
            {
                "resource": "api",
                "auth_class": "A-machine-native",
                "credential_ref": "env:T",
                "curated": True,
            },
            {
                "resource": "web",
                "auth_class": "B-bootstrap-once",
                "credential_ref": "store:tc_1",
                "curated": True,
            },
        ]
    }
    m = map_access_for_tfactory(block)
    assert m["ready"] == ["api", "web"]
    assert m["needs_egress"] is True
    assert m["credential_refs"] == ["env:T", "store:tc_1"]
    assert m["blocked"] == [] and val3_blocked(m) is False


def test_uncurated_and_class_D_are_blocked_with_reasons():
    block = {
        "requirements": [
            {
                "resource": "staging",
                "auth_class": "B-bootstrap-once",
                "credential_ref": "store:x",
            },  # not curated
            {
                "resource": "mfa",
                "auth_class": "D-un-automatable",
                "mvp_note": "push approval",
            },
        ]
    }
    m = map_access_for_tfactory(block)
    assert m["ready"] == [] and m["needs_egress"] is False
    by = {b["resource"]: b["reason"] for b in m["blocked"]}
    assert "not curated" in by["staging"]
    assert by["mfa"] == "push approval"
    assert val3_blocked(m) is True  # honest: VAL-3 cannot run for the whole task


def test_curated_without_ref_still_ready_no_ref():
    block = {
        "requirements": [
            {"resource": "k8s", "auth_class": "C-ephemeral-target", "curated": True},
        ]
    }
    m = map_access_for_tfactory(block)
    assert (
        m["ready"] == ["k8s"]
        and m["credential_refs"] == []
        and m["needs_egress"] is True
    )

"""RFC-0007 (#87 PR-2): parse_tfactory_profile surfaces the access mapping."""

from __future__ import annotations

from agents.task_contract import parse_tfactory_profile

_ACCESS = {
    "requirements": [
        {
            "resource": "api",
            "auth_class": "A-machine-native",
            "credential_ref": "env:T",
            "curated": True,
        },
        {"resource": "mfa", "auth_class": "D-un-automatable", "mvp_note": "push"},
    ]
}


def test_access_attached_alongside_tfactory_block():
    prof = parse_tfactory_profile({"tfactory": {"lanes": ["unit"]}, "access": _ACCESS})
    assert prof is not None
    assert prof.access["ready"] == ["api"]
    assert prof.access["credential_refs"] == ["env:T"]
    assert prof.access["blocked"][0]["resource"] == "mfa"


def test_access_only_contract_still_yields_profile():
    prof = parse_tfactory_profile({"access": _ACCESS})  # no tfactory block
    assert prof is not None and prof.access["ready"] == ["api"]


def test_no_access_no_block_is_none():
    assert parse_tfactory_profile({"contract_version": "2"}) is None


def test_access_defaults_empty_when_absent():
    prof = parse_tfactory_profile({"tfactory": {"lanes": ["unit"]}})
    assert prof is not None and prof.access == {
        "needs_egress": False,
        "credential_refs": [],
        "ready": [],
        "blocked": [],
    }

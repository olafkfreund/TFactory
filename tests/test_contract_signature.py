"""#1432: PFactory Ed25519 contract signature verified at TFactory ingest.

Signed contracts are built with the RFC 8032 TEST 1 seed from the shared golden
fixture (a public test vector); no key is generated here.
"""

from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.contract_signature import (  # noqa: E402
    _signing_bytes,
    apply_overlay,
    check_repo_binding,
    verify_contract_signature,
)
from agents.tools_pkg.tools.task_control import (  # noqa: E402
    create_spec_ingest_workspace,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "contract_signature_golden.json").read_text()
)
PUBKEY_VAR = "TFACTORY_CONTRACT_PUBKEY_PFACTORY__GOLDEN"
SIG_MODE = "TFACTORY_CONTRACT_SIGNATURE_MODE"
SCHEMA_MODE = "TFACTORY_CONTRACT_SCHEMA_MODE"
MD = "# T\n\n## Acceptance Criteria\n- it works\n"


@pytest.fixture(autouse=True)
def _keyring(monkeypatch):
    monkeypatch.setenv(PUBKEY_VAR, GOLDEN["public_key"])
    for var in (SIG_MODE, SCHEMA_MODE, "TFACTORY_CONTRACT_RETIRED_KIDS"):
        monkeypatch.delenv(var, raising=False)


def _sign(contract: dict, **over) -> dict:
    """Return ``contract`` with a valid approval.pubkey_signature (golden seed)."""
    c = copy.deepcopy(contract)
    env = {
        "approved_by": GOLDEN["approved_by"],
        "approval_timestamp": GOLDEN["approval_timestamp"],
        "plan_contract_version": GOLDEN["contract_version"],
        "signature": "hmac-not-checked-here",  # schema requires the HMAC field
        **over,
    }
    key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(GOLDEN["test_only_seed"])
    )
    msg = _signing_bytes(
        c, env["approved_by"], env["approval_timestamp"],
        env["plan_contract_version"], kid=GOLDEN["kid"],
    )  # fmt: skip
    env["pubkey_signature"] = {
        "alg": "ed25519",
        "kid": GOLDEN["kid"],
        "sig": base64.b64encode(key.sign(msg)).decode(),
    }
    c["approval"] = env
    return c


def _golden_signed() -> dict:
    c = copy.deepcopy(GOLDEN["contract"])
    c["approval"] = {
        "approved_by": GOLDEN["approved_by"],
        "approval_timestamp": GOLDEN["approval_timestamp"],
        "plan_contract_version": GOLDEN["contract_version"],
        "pubkey_signature": {
            "alg": "ed25519",
            "kid": GOLDEN["kid"],
            "sig": GOLDEN["sig"],
        },
    }
    return c


# ---- verify_contract_signature ---------------------------------------------


def test_golden_vector_verifies():
    assert verify_contract_signature(_golden_signed()) == []


def test_changed_tfactory_block_is_bad_signature():
    c = _golden_signed()
    c["tfactory"]["lanes"] = ["unit"]
    assert verify_contract_signature(c) == ["bad_signature"]


def test_changed_timestamp_is_bad_signature():
    c = _golden_signed()
    c["approval"]["approval_timestamp"] = "2027-01-01T00:00:00Z"
    assert verify_contract_signature(c) == ["bad_signature"]


def test_unknown_kid(monkeypatch):
    monkeypatch.delenv(PUBKEY_VAR)
    assert verify_contract_signature(_golden_signed()) == ["unknown_kid"]


@pytest.mark.parametrize("token", ["pfactory/golden", "golden", "PFactory/GOLDEN"])
def test_retired_kid(monkeypatch, token):
    monkeypatch.setenv("TFACTORY_CONTRACT_RETIRED_KIDS", f"other, {token}")
    assert verify_contract_signature(_golden_signed()) == ["retired_kid"]


def test_missing_pubkey_signature_is_unsigned():
    c = _golden_signed()
    del c["approval"]["pubkey_signature"]
    assert verify_contract_signature(c) == ["unsigned"]
    assert verify_contract_signature({"tfactory": {}}) == ["unsigned"]


def test_bad_alg():
    c = _golden_signed()
    c["approval"]["pubkey_signature"]["alg"] = "rsa"
    assert verify_contract_signature(c) == ["bad_alg"]


def test_bad_base64_is_malformed():
    c = _golden_signed()
    c["approval"]["pubkey_signature"]["sig"] = "not base64!!"
    assert verify_contract_signature(c) == ["malformed"]


# ---- apply_overlay ---------------------------------------------------------


def test_overlay_ignores_non_dict_forwarded_blocks():
    signed = {"execution": "x", "provenance": ["y"]}
    fwd = {"execution": "bad", "provenance": 3}
    assert apply_overlay(signed, fwd) == signed
    fwd = {
        "execution": {"phase_models": {"qa": "m"}},
        "provenance": {"github_issue": 1},
    }
    assert apply_overlay(signed, fwd) == signed


def test_overlay_rules():
    signed = {
        "execution": {"phase_models": {"coding": "signed"}, "x": 1},
        "provenance": {"repo": "o/r", "github_issue": 1},
        "correlation_key": "k1",
        "tfactory": {"lanes": ["unit"]},
    }
    fwd = {
        "execution": {"phase_models": {"coding": "fwd", "qa": "fwd-qa"}, "x": 2},
        "provenance": {"github_issue": 2, "repo": "evil/repo"},
        "correlation_key": "k2",
        "tfactory": {"lanes": ["api"]},
        "extra": True,
    }
    out = apply_overlay(signed, fwd)
    assert out["execution"]["phase_models"] == {"coding": "signed", "qa": "fwd-qa"}
    assert out["execution"]["x"] == 1
    assert out["provenance"] == {"repo": "o/r", "github_issue": 1}
    assert out["correlation_key"] == "k1"
    assert out["tfactory"] == {"lanes": ["unit"]}
    assert "extra" not in out
    assert signed["execution"]["phase_models"] == {"coding": "signed"}  # not mutated


def test_overlay_adds_missing_fields():
    out = apply_overlay(
        {"tfactory": {}},
        {"provenance": {"github_issue": 5}, "correlation_key": "o/r#5"},
    )
    assert out["provenance"]["github_issue"] == 5
    assert out["correlation_key"] == "o/r#5"


# ---- check_repo_binding ----------------------------------------------------


@pytest.mark.parametrize(
    "repo",
    [
        "o/r",
        "O/R",
        "https://github.com/o/r.git",
        "git@github.com:o/r.git",
        "github:o/r",
    ],
)
def test_repo_binding_match(repo):
    assert check_repo_binding({"provenance": {"repo": "o/r"}}, [repo, None]) == []


def test_repo_binding_mismatch_and_unbound():
    c = {"provenance": {"repo": "o/r"}}
    assert check_repo_binding(c, ["o/r", "a/b"]) == ["repo_mismatch"]
    assert check_repo_binding({"provenance": {}}, ["o/r"]) == ["repo_unbound"]
    assert check_repo_binding({}, ["o/r"]) == ["repo_unbound"]
    assert check_repo_binding(c, [None, ""]) == ["repo_unbound"]


# ---- ingest wiring ---------------------------------------------------------

_BASE = {
    "contract_version": "2",
    "feature": "f",
    "workflow_type": "feature",
    "phases": [
        {
            "phase": 1,
            "name": "p",
            "subtasks": [{"id": "1.1", "description": "d", "status": "pending"}],
        }
    ],
    "tfactory": {"lanes": ["unit"]},
    "provenance": {"repo": "o/r"},
}


def _ingest(tmp_path, **kw):
    kw.setdefault("repo_ref", "o/r")
    return create_spec_ingest_workspace(
        project_id="proj", spec_id=kw.pop("spec_id", "s1"), spec_text=MD, root=tmp_path,
        schedule=False, **kw,
    )  # fmt: skip


def _ctx(tmp_path) -> Path:
    return tmp_path / "workspaces" / "proj" / "specs" / "s1"


def _sig_warnings(result) -> list[str]:
    return [w for w in result["warnings"] if "signature" in w]


def test_warn_mode_tampered_contract_ingests_with_one_warning(tmp_path):
    c = _sign(_BASE)
    c["tfactory"]["lanes"] = ["unit", "api"]
    result = _ingest(tmp_path, contract=c)
    assert len(_sig_warnings(result)) == 1 and "bad_signature" in result["warnings"][0]
    assert (_ctx(tmp_path) / "context" / "task_contract.json").exists()


def test_enforce_mode_tampered_contract_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    c = _sign(_BASE)
    c["tfactory"]["lanes"] = ["unit", "api"]
    with pytest.raises(ValueError, match="signature invalid: bad_signature"):
        _ingest(tmp_path, contract=c)
    assert not _ctx(tmp_path).exists()


def test_unknown_mode_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "bogus")
    with pytest.raises(ValueError, match="signature invalid"):
        _ingest(tmp_path, contract=_sign(_BASE), repo_ref="a/b")


def test_enforce_rejects_unsigned(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    with pytest.raises(ValueError, match="unsigned"):
        _ingest(tmp_path, contract=_BASE)


def test_valid_signed_contract_with_overlay_writes_signed_block(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    signed = _sign(_BASE)
    forwarded = copy.deepcopy(signed)
    forwarded["tfactory"] = {"lanes": ["api"]}  # AIFactory must not change this
    forwarded["provenance"]["github_issue"] = 7
    result = _ingest(tmp_path, contract=forwarded, signed_contract=signed)
    assert _sig_warnings(result) == []
    saved = json.loads((_ctx(tmp_path) / "context" / "task_contract.json").read_text())
    assert saved["tfactory"] == {"lanes": ["unit"]}
    assert saved["provenance"]["github_issue"] == 7


def _pm_contracts(signed_pm, fwd_pm):
    signed = copy.deepcopy(_BASE)
    if signed_pm:
        signed["execution"] = {"phase_models": signed_pm}
    signed = _sign(signed)
    fwd = copy.deepcopy(signed)
    fwd["execution"] = {"phase_models": fwd_pm}
    return signed, fwd


def _models(tmp_path) -> dict:
    return json.loads((_ctx(tmp_path) / "task_metadata.json").read_text())[
        "phaseModels"
    ]


def test_forwarded_phase_models_added_when_signed_has_none(tmp_path):
    signed, fwd = _pm_contracts(None, {"coding": "ollama:x"})
    _ingest(tmp_path, contract=fwd, signed_contract=signed)
    assert _models(tmp_path) == {"coding": "ollama:x"}


def test_signed_phase_models_win_and_new_keys_are_added(tmp_path):
    signed, fwd = _pm_contracts({"coding": "signed"}, {"coding": "fwd", "qa": "fwd-qa"})
    _ingest(tmp_path, contract=fwd, signed_contract=signed)
    assert _models(tmp_path) == {"coding": "signed", "qa": "fwd-qa"}


def test_repo_mismatch_warn_and_enforce(tmp_path, monkeypatch):
    signed = _sign(_BASE)
    result = _ingest(tmp_path, contract=signed, repo_ref="other/repo")
    assert "repo_mismatch" in _sig_warnings(result)[0]
    monkeypatch.setenv(SIG_MODE, "enforce")
    with pytest.raises(ValueError, match="repo_mismatch"):
        _ingest(tmp_path, contract=signed, repo_ref="other/repo", spec_id="s2")


def test_clone_url_is_bound_too(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    ok = _ingest(
        tmp_path, contract=_sign(_BASE), clone_url="https://github.com/o/r.git"
    )
    assert _sig_warnings(ok) == []
    with pytest.raises(ValueError, match="repo_mismatch"):
        _ingest(
            tmp_path, contract=_sign(_BASE), clone_url="https://github.com/x/y.git",
            spec_id="s2",
        )  # fmt: skip


def test_swapped_contract_failing_schema_gives_schema_warning(tmp_path, monkeypatch):
    monkeypatch.setenv(SCHEMA_MODE, "warn")
    signed = _sign({**_BASE, "phases": "x"})
    result = _ingest(tmp_path, contract=signed, signed_contract=signed)
    assert any(w.startswith("task contract fails schema") for w in result["warnings"])
    assert _sig_warnings(result) == []


def test_contract_without_markers_is_not_checked(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    result = _ingest(tmp_path, contract={"provenance": {"repo": "o/r"}})
    assert result["warnings"] == []
    assert not (_ctx(tmp_path) / "context" / "task_contract.json").exists()


def test_build_only_contract_without_markers_still_routes_models(tmp_path, monkeypatch):
    monkeypatch.setenv(SIG_MODE, "enforce")
    _ingest(tmp_path, contract={"execution": {"phase_models": {"coding": "ollama:x"}}})
    assert _models(tmp_path) == {"coding": "ollama:x"}

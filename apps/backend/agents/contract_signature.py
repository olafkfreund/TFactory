"""Verify the PFactory Ed25519 approval signature on a Task Contract (#1432).

Returns reason codes only, never the value or key that failed (#718).
"""

from __future__ import annotations

import base64
import binascii
import copy
import json
import os
from collections.abc import Mapping
from typing import Any

import repo_ref
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_PUBKEY_PREFIX = "TFACTORY_CONTRACT_PUBKEY_"
_RETIRED_ENV = "TFACTORY_CONTRACT_RETIRED_KIDS"
_SEP = "__"


# Copied from PFactory apps/backend/plan/emit/signing.py (_canonical, _plan_core,
# _signing_bytes). They must stay byte-identical; the golden vector in
# tests/fixtures/contract_signature_golden.json guards that.
def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_core(contract: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in contract.items() if k != "approval"}


def _signing_bytes(
    contract: dict[str, Any],
    approved_by: str,
    approval_timestamp: str,
    contract_version: str,
    kid: str | None = None,
) -> bytes:
    parts = [
        _canonical(_plan_core(contract)),
        approved_by,
        approval_timestamp,
        contract_version,
    ]
    if kid:
        parts.append(kid)
    return "|".join(parts).encode("utf-8")


def load_pubkeys(env: Mapping[str, str] | None = None) -> dict[str, bytes]:
    """``{"authority/kid": raw 32-byte key}`` from TFACTORY_CONTRACT_PUBKEY_<AUTH>__<KID>."""
    env = os.environ if env is None else env
    keys: dict[str, bytes] = {}
    for name, value in env.items():
        if not name.upper().startswith(_PUBKEY_PREFIX) or not value:
            continue
        authority, sep, kid = name[len(_PUBKEY_PREFIX) :].lower().partition(_SEP)
        if not sep or not authority or not kid:
            continue
        try:
            keys[f"{authority}/{kid}"] = base64.b64decode(value.strip(), validate=True)
        except (ValueError, binascii.Error):
            continue  # a bad key can only fail closed as unknown_kid
    return keys


def retired(env: Mapping[str, str] | None = None) -> set[str]:
    env = os.environ if env is None else env
    return {
        t.strip().lower() for t in (env.get(_RETIRED_ENV) or "").split(",") if t.strip()
    }


def _envelope(contract: Any) -> tuple[dict[str, Any], str, str, str] | str:
    """``(approval, authority, kid, sig_b64)``, or the reason code it is unusable."""
    approval = contract.get("approval") if isinstance(contract, dict) else None
    if not isinstance(approval, dict):
        return "unsigned"
    sig = approval.get("pubkey_signature")
    if not isinstance(sig, dict):
        return "unsigned"
    if sig.get("alg") != "ed25519":
        return "bad_alg"
    kid, sig_b64, authority = (
        sig.get("kid"),
        sig.get("sig"),
        approval.get("approved_by"),
    )
    if not (
        isinstance(kid, str)
        and kid
        and isinstance(sig_b64, str)
        and sig_b64
        and isinstance(authority, str)
        and authority
    ):
        return "malformed"
    return approval, authority, kid, sig_b64


def verify_contract_signature(
    contract: Any, env: Mapping[str, str] | None = None
) -> list[str]:
    """Reason codes; ``[]`` means the Ed25519 signature is valid."""
    parsed = _envelope(contract)
    if isinstance(parsed, str):
        return [parsed]
    approval, authority, kid, sig_b64 = parsed
    # Lowercase only for lookup; the verified bytes carry the kid as signed.
    ref = f"{authority.lower()}/{kid.lower()}"
    if retired(env) & {ref, kid.lower()}:
        return ["retired_kid"]
    raw = load_pubkeys(env).get(ref)
    if raw is None:
        return ["unknown_kid"]
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(
            base64.b64decode(sig_b64, validate=True),
            _signing_bytes(
                contract,
                authority,
                str(approval.get("approval_timestamp", "")),
                str(approval.get("plan_contract_version", "")),
                kid=kid,
            ),
        )
    except InvalidSignature:
        return ["bad_signature"]
    except (ValueError, binascii.Error):
        return ["malformed"]
    return []


def _sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    """``d[key]`` when it is a dict, else ``{}`` (the forwarded copy is untrusted)."""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def apply_overlay(signed: dict[str, Any], forwarded: Any) -> dict[str, Any]:
    """``signed`` plus the three add-only fields AIFactory overlays; nothing else."""
    out = copy.deepcopy(signed)
    if not isinstance(forwarded, dict):
        return out
    fwd_pm = _sub(forwarded, "execution").get("phase_models")
    if (
        isinstance(fwd_pm, dict)
        and fwd_pm
        and isinstance(out.get("execution", {}), dict)
    ):
        execution = out.setdefault("execution", {})
        execution["phase_models"] = {
            **copy.deepcopy(fwd_pm),
            **_sub(execution, "phase_models"),
        }
    issue = _sub(forwarded, "provenance").get("github_issue")
    if issue is not None and isinstance(out.get("provenance", {}), dict):
        out.setdefault("provenance", {}).setdefault("github_issue", issue)
    if forwarded.get("correlation_key") is not None:
        out.setdefault("correlation_key", forwarded["correlation_key"])
    return out


def _owner_name(ref: Any) -> str:
    value = repo_ref.project_of(ref if isinstance(ref, str) else None).strip()
    if "://" in value:
        value = value.split("://", 1)[1].partition("/")[2]
    elif "@" in value.split("/", 1)[0] and ":" in value:  # git@host:owner/name
        value = value.partition(":")[2]
    return value.lstrip("/").removesuffix(".git").lower()


def check_repo_binding(contract: Any, request_repos: list[Any]) -> list[str]:
    """The signed ``provenance.repo`` must equal every repo the request names."""
    prov = contract.get("provenance") if isinstance(contract, dict) else None
    signed = _owner_name(prov.get("repo")) if isinstance(prov, dict) else ""
    present = [r for r in (_owner_name(x) for x in request_repos) if r]
    if not signed or not present:
        return ["repo_unbound"]
    return ["repo_mismatch"] if any(r != signed for r in present) else []

---
status: approved
issue: 1432
spec: spec/2026-10-26-1432-contract-signature-verify.md
---

# Plan: verify the contract approval signature in TFactory (#1432)

## Approved decisions (from the spec)

- TFactory checks an **Ed25519** signature with the signer's **public key**.
  AIFactory **forwards it unchanged and does not re-sign**. The HMAC
  `approval.signature` that AIFactory checks stays exactly as it is.
- **Signed bytes:** PFactory's existing `_signing_bytes(contract, approved_by,
  approval_timestamp, plan_contract_version, kid=<ed kid>)`, which is the
  canonical JSON (`sort_keys=True, separators=(",", ":"), ensure_ascii=False`,
  UTF-8) of the contract minus `approval`, then `|`-joined metadata. The
  Ed25519 kid is required. This is Python's `json.dumps` output, not JCS:
  raw UTF-8 for non-ASCII, float `repr`, `NaN` allowed. A non-Python signer
  must reproduce it byte for byte, checked by the golden vector. The kid is
  lowercased at signing (PFactory `signing.py:109`). TFactory lowercases it
  only for the keyring lookup, never inside the verified bytes.
- **Envelope:** `approval.pubkey_signature = {"alg": "ed25519", "kid": ..., "sig": <base64>}`.
  It is optional. PFactory adds it only when it also HMAC-signs and an
  Ed25519 key is configured.
- **Forwarding:** AIFactory adds a top-level `signed_contract` to the ingest
  payload. It is the contract before AIFactory's overlay, sent only when the
  contract has `approval` and is not held. `contract` itself is unchanged.
- **TFactory:** verify `signed_contract` if present, else `contract`. When
  `signed_contract` is valid, persist and use it, plus the overlay taken from
  `contract` (`execution.phase_models` merged with signed keys winning, so
  the overlay may only add keys the signed copy does not set,
  `provenance.github_issue` set only if missing, `correlation_key` set only if
  missing). A caller choosing a weak verify model this way is an accepted
  risk (spec Risks). Reason
  codes are `unsigned`, `unknown_kid`, `retired_kid`, `bad_alg`,
  `bad_signature`, `malformed`, `repo_mismatch` and `repo_unbound`. Never log
  values or keys.
- **Repo binding:** after the signature verifies, the signed
  `provenance.repo` must name the same `owner/name` as every repo the request
  carries (`repo`, and `git_url` or the registered project's `clonedFrom`).
  Mismatch is `repo_mismatch`; a missing side is `repo_unbound`. Same mode as
  the signature. The signature proves who wrote the contract and the test
  profile. It does not prove the build branch or the acceptance criteria,
  which come from the unsigned `spec_text`.
- **Schema check:** the #1427 validation runs on the persisted contract
  (`signed_contract` plus the overlay) after the swap.
- **Mode:** `TFACTORY_CONTRACT_SIGNATURE_MODE`, `warn` by default. Unknown
  values fail closed, the same as `TFACTORY_CONTRACT_SCHEMA_MODE`. Enforce
  rejects `unsigned` too. This work ships warn only.
- **Keys:** the PFactory private key is `PFACTORY_CONTRACT_SIGNING_KEY__<KID>`
  (base64, 32 raw bytes) in `factory-secrets` through agenix, with pin
  `PFACTORY_CONTRACT_SIGNING_KID`. The TFactory public keys are
  `TFACTORY_CONTRACT_PUBKEY_<AUTHORITY>__<KID>` (base64, 32 raw bytes), each a
  plain `value:` in the gitops manifest. Revocation is
  `TFACTORY_CONTRACT_RETIRED_KIDS` (`authority/kid` or a bare `kid`).

## Steps

The repos are released in this order: PFactory, then AIFactory, then
TFactory, then gitops. Every step is backward compatible on its own.

1. **Golden vector.** `TFactory: tests/fixtures/contract_signature_golden.json`
   (new). It holds a small v2 contract, a test-only Ed25519 seed (base64, which
   is not a real key), the kid `golden`, the `approved_by`, timestamp and
   version, and the expected `sig`. Generate it once with a short script that
   uses `cryptography` and is not committed. Copy the same file byte for byte
   to `PFactory: tests/fixtures/contract_signature_golden.json`.
   → verify: `sha256sum` matches in both repos.
   Traps: name the seed `test_only_seed` so secret scanners and reviewers can
   see what it is. Add it to the seclint allowlist if it is flagged.

2. **PFactory signing.** `PFactory: apps/backend/plan/emit/signing.py`:
   - `key_from_env` (lines 83-128): add `prefix: str = _ENV_KEY_PREFIX` and
     `kid_env: str = _KID_ENV` keyword args. The defaults keep every caller
     byte-identical.
   - New `sign_pubkey(contract, *, seed_b64, kid, approved_by,
     approval_timestamp, contract_version) -> dict`, which uses
     `Ed25519PrivateKey.from_private_bytes` over
     `_signing_bytes(..., kid=kid)` and returns `{"alg", "kid", "sig"}`.
   - `PFactory: apps/backend/plan/emit/contract_emit.py:353-362`: after
     `attach_signature`, resolve
     `key_from_env("", prefix="PFACTORY_CONTRACT_SIGNING_KEY", kid_env="PFACTORY_CONTRACT_SIGNING_KID")`.
     An empty authority makes the keyed prefix `PFACTORY_CONTRACT_SIGNING_KEY__`
     (line 106 builds `f"{prefix}{authority.upper()}"` once `prefix` is a parameter). If a key and a kid are found, set
     `contract["approval"]["pubkey_signature"] = sign_pubkey(...)` using the
     envelope's own `approved_by`, timestamp and version.
     A key with no kid (the unkeyed legacy var) is ignored with a warning and
     no Ed25519 signature is added.
   - `PFactory: apps/backend/requirements.txt`: add `cryptography>=42`. It is
     already installed through a transitive dependency.
   - The Ed25519 setup is optional and must never break the HMAC fast path.
     Wrap the whole Ed25519 block (the `key_from_env` call, whose `ValueError`
     covers ambiguous or bad kids, and `sign_pubkey`, whose
     `from_private_bytes` raises `ValueError` on a bad seed, plus
     `binascii.Error` on bad base64) in `try/except (ValueError,
     binascii.Error)`. On failure, log a warning naming the exception type
     only, never the seed, and emit the contract without `pubkey_signature`.
     The HMAC `signature` that `attach_signature` already set is untouched.
   - Tests: `PFactory: tests/test_contract_pubkey_signing.py` (new) covers the
     golden vector, that the HMAC `signature` is identical with and without the
     Ed25519 env, that nothing is added without the env, that a kid-less
     key adds no Ed25519 signature, and that a bad seed (wrong length, bad
     base64) and an ambiguous kid each log a warning and emit the HMAC-signed
     contract with no `pubkey_signature`.
   → verify: `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_contract_pubkey_signing.py ../../tests/test_contract_parallel_signing.py -q`, plus `ruff` and `scripts/ratchet_lint.py`.
   Traps: never log the seed. `signing.py` must stay a mirror of AIFactory's
   `_signing_bytes`, so do not touch that function.

3. **AIFactory forwarding.** `AIFactory: apps/backend/pfactory/tfactory_client.py:692-711`:
   right after `contract = load_task_contract(...) if contract is None else dict(contract)`
   (line 693), capture
   `signed = copy.deepcopy(contract) if not held and isinstance(contract, dict) and isinstance(contract.get("approval"), dict) else None`.
   After `payload["contract"] = contract` (line 711), add
   `if signed: payload["signed_contract"] = signed`. Update the docstring to
   name the three overlay paths.
   - Tests: `AIFactory: apps/backend/pfactory/test_tfactory_client_issue.py`
     covers a signed contract plus an issue or phase_models overlay. Then
     `payload["signed_contract"]` equals the original and has no
     `provenance.github_issue` added, and `payload["contract"]` still has the
     overlay. For held (`{}`) and unsigned contracts, there is no
     `signed_contract`.
   → verify: `cd apps/backend && .venv/bin/python -m pytest pfactory/test_tfactory_client_issue.py -q`.
   Traps: use `copy.deepcopy`, because `dict(contract)` is shallow and later
   edits must never reach `signed_contract`. Do not change `contract`.
   `build_ingest_payload` is at its statement ceiling (see the comment near
   line 735), so keep the change to two statements or move the capture into a
   helper.

4. **TFactory verifier.** `TFactory: apps/backend/agents/contract_signature.py` (new, about 80 lines):
   - Copy `_canonical`, `_plan_core` and `_signing_bytes` from PFactory
     `signing.py:48-80`, with a comment that names the source.
   - `load_pubkeys(env) -> dict["authority/kid", bytes]` reads every
     `TFACTORY_CONTRACT_PUBKEY_<AUTH>__<KID>`, lowercased. An entry without
     `__` is ignored.
   - `retired(env) -> set[str]` reads `TFACTORY_CONTRACT_RETIRED_KIDS`.
   - `verify_contract_signature(contract, env=None) -> list[str]`: a missing
     `approval` or `pubkey_signature` returns `["unsigned"]`, an alg other than
     `ed25519` returns `["bad_alg"]`, a retired kid returns `["retired_kid"]`, a
     key not in the keyring returns `["unknown_kid"]`, a base64 or key error
     returns `["malformed"]`, an `InvalidSignature` returns
     `["bad_signature"]`, and a valid signature returns `[]`.
   - `apply_overlay(signed, forwarded) -> dict` deep-copies `signed` and
     applies the three add-only rules from the decisions above
     (`execution.phase_models` with signed keys winning,
     `provenance.github_issue`, `correlation_key`). It copies nothing else
     from `forwarded`.
   - `check_repo_binding(contract, request_repos) -> list[str]`:
     `["repo_unbound"]` when `provenance.repo` is missing or `request_repos`
     has no non-empty value, `["repo_mismatch"]` when any present value names
     another `owner/name`, else `[]`. Normalise with `repo_ref.project_of`,
     then for a URL (`://` or the `git@host:` form) keep only the path, strip
     a leading `/` and a trailing `.git`, and lowercase.
   - `TFactory: apps/backend/requirements.txt`: add `cryptography>=42`. It is
     already present at 49.0.0 through a transitive dependency.
   → verify: step 6 tests.
   Traps: catch only `InvalidSignature`, `ValueError` and `binascii.Error`,
   never a bare `except`. Return codes only, never the value that failed (#718).

5. **TFactory ingest wiring.**
   - `TFactory: apps/web-server/server/routes/specs.py:79-86`: add
     `signed_contract: dict | None = Field(default=None, description=...)`
     after `contract`, and pass `signed_contract=req.signed_contract` and
     `clone_url=req.git_url or entry.get("clonedFrom")` in the call at lines
     313-325. `repo_ref=req.repo` is already passed.
   - `TFactory: apps/backend/agents/tools_pkg/tools/task_control.py`:
     add the parameters `signed_contract: dict | None = None` and
     `clone_url: str | None = None` (line 455).
     Inside the marker block (lines 505-522), in this order:
     1. `sig_errors = verify_contract_signature(signed_contract if isinstance(signed_contract, dict) else contract)`.
     2. When it is empty, add (with `verified` the dict just checked)
        `check_repo_binding(verified, [repo_ref, clone_url])` to `sig_errors`.
     3. When the signature verified and `signed_contract` is a dict, set
        `contract = apply_overlay(signed_contract, contract)`.
     4. Run the existing #1427 schema check on that `contract`, so it
        validates the persisted contract after the swap.
     5. When `sig_errors` is not empty, apply
        `TFACTORY_CONTRACT_SIGNATURE_MODE` exactly as the schema mode block
        does: `warn` logs `"task contract signature invalid for %r: %s"` with
        the codes and appends to `warnings`, and anything else raises
        `ValueError("task contract signature invalid: <codes>")`.
     Extend the docstring "Raises" note (line 474).
   - Do the same for `docs/capabilities-and-usage.md:204`: add one sentence on
     `TFACTORY_CONTRACT_SIGNATURE_MODE` and the `TFACTORY_CONTRACT_PUBKEY_*` vars.
   → verify: step 6 tests, plus `ruff check` and `scripts/ratchet_lint.py`
   on the touched files.
   Traps: the swapped `contract` must be in place before the
   `task_contract.json` write (line 540) and before the `phase_models` read
   (lines 548-561). The swap happens inside the marker predicate, but the
   `phase_models` read stays **outside** it on purpose: build-only contracts
   carry no markers and still route models (see the comment at lines
   635-637). Do not move that read into the marker block. A statement-count lint may
   bite `create_spec_ingest_workspace`. If it does, move the mode handling for
   both gates into one `_contract_gate(errors, label, env_var, warnings)`
   helper.

6. **TFactory tests.** `TFactory: tests/test_contract_signature.py` (new):
   - The golden vector verifies.
   - These cases each return their code: a changed `tfactory` block gives
     `bad_signature`, a changed `approval_timestamp` gives `bad_signature`,
     an unknown kid gives `unknown_kid`, a retired kid gives `retired_kid`
     (both the `authority/kid` and the bare `kid` form), a missing
     `pubkey_signature` gives `unsigned`, `alg: "rsa"` gives `bad_alg`, and
     bad base64 gives `malformed`.
   - `apply_overlay`: signed `phase_models` keys keep their signed values,
     forwarded keys the signed copy does not set are added, `github_issue` and
     `correlation_key` are only added when missing, and other forwarded fields
     are dropped.
   - `check_repo_binding`: a match (`owner/name`, an https URL with `.git`, a
     `git@` URL, different case) gives `[]`, another repo gives
     `repo_mismatch`, and a missing `provenance.repo` or no request repo gives
     `repo_unbound`.
   - Ingest, through `create_spec_ingest_workspace` like
     `tests/test_spec_ingest_workspace.py:51`. In warn mode a tampered contract
     still ingests and gives one warning. In enforce mode it raises
     `ValueError`. A valid `signed_contract` with an overlaid `contract` writes
     the signed `tfactory` block to `task_contract.json` and gives no warning.
     A valid `signed_contract` without `phase_models`, with a forwarded
     `contract` that adds `execution.phase_models`, writes those models to
     `task_metadata.json`; with signed `phase_models`, a forwarded value for a
     signed key does not reach `task_metadata.json`, and a forwarded new key
     does. A valid signature with another
     `repo_ref` warns `repo_mismatch` in warn mode and raises in enforce mode.
     A valid signature whose swapped contract fails the schema gives the
     schema warning. A contract without markers is not checked, and a
     build-only contract without markers still writes `task_metadata.json`.
   → verify: `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_contract_signature.py ../../tests/test_spec_ingest_workspace.py ../../tests/test_task_contract.py -q`.
   Traps: set the env with `monkeypatch.setenv` or `env=`, never by changing
   `os.environ` directly.

7. **Keys and gitops** (operator step, after the three releases).
   - Make the key pair. The private half goes into `factory-secrets` as
     `PFACTORY_CONTRACT_SIGNING_KEY__2026q4` through
     `agenix -e`, non-interactively without a tty, and never echoed.
   - `factory-gitops: apps/pfactory/manifests/manifests.yaml:283`: add a
     `secretKeyRef` env for it, plus `PFACTORY_CONTRACT_SIGNING_KID: "2026q4"`.
   - `factory-gitops: apps/tfactory/manifests/manifests.yaml:467`: add
     `{ name: TFACTORY_CONTRACT_PUBKEY_PFACTORY__2026Q4, value: "<base64 public key>" }`.
     Leave `TFACTORY_CONTRACT_SIGNATURE_MODE` unset, which means warn.
   - `factory-gitops: docs/secrets-management.md:44`: add the new key to the
     `factory-secrets` row.
   → verify: after sync, run one PARR build. The TFactory log has no
   `signature invalid` line and the stored `context/task_contract.json` has
   `approval.pubkey_signature`.
   Traps: the public key is the only key material TFactory gets. Never put the
   private key in a `value:`.

Flipping to `enforce` is not part of this plan. It waits until warn mode is
clean and the spec's open question is answered: do proof runs (the #1431
template) get a PFactory-signed contract, or send none?

## Deviations during implementation

- Step 1: the golden `test_only_seed` is the published RFC 8032 §7.1 TEST 1
  secret key, not a newly generated seed, because no key may be generated in
  this wave. The fixture also carries `public_key` and a `_note`. The contract
  is hand-built in the v2 shape; it is not a schema-valid contract, so the
  TFactory ingest tests use their own small valid contract signed with the same
  seed.
- Step 2: the PFactory Ed25519 block is a helper, `_attach_pubkey_signature`.
  `sign_pubkey` and the helper carry full type arguments for the mypy ratchet.
- Step 3: the capture is the helper `_signed_snapshot` (the alternative the
  step allows). The new `payload["signed_contract"]` line has a
  `type: ignore[assignment]`, because `payload` is inferred as `dict[str, str]`.
- Step 4: the keyring authority is `approval.approved_by`, lowercased (spec).
  `apply_overlay` ignores a forwarded or signed `execution`/`provenance` that
  is not a dict, so it can't raise a 500 on a malformed caller payload.
- Step 5: `_contract_gate` was not needed. The schema check runs before the
  signature gate, so a contract that fails both raises the schema error first in
  enforce mode. `test_spec_ingest_workspace.py::test_valid_contract_passes_in_enforce_mode`
  now matches only the schema warning, since an unsigned contract also warns.

## Tests

- PFactory: `pytest tests/test_contract_pubkey_signing.py tests/test_contract_parallel_signing.py -q`, all passing.
- AIFactory: `pytest apps/backend/pfactory/test_tfactory_client_issue.py -q`, all passing.
- TFactory: `pytest tests/test_contract_signature.py tests/test_spec_ingest_workspace.py tests/test_task_contract.py -q`, all passing. `python scripts/check_schema_drift.py` still passes because the schema is not changed.
- Regression proof: put back the TFactory ingest without the verifier call,
  and the enforce-mode tamper test fails. Without the repo check, the enforce
  `repo_mismatch` test fails. If the overlay lets a forwarded
  `phase_models` value replace a signed one, the phase-models test fails.

## Rollback

- Fastest: `TFACTORY_CONTRACT_SIGNATURE_MODE=warn` stops any rejection. It is
  already the default.
- To stop signing: remove `PFACTORY_CONTRACT_SIGNING_KEY__*` from the PFactory
  env. Contracts then go out without `pubkey_signature`, and TFactory warns
  `unsigned`, which does no harm in warn mode.
- Code: revert each repo's PR. Every change is additive. The new fields are
  optional and ignored by older peers, so the repos can be reverted in any
  order.

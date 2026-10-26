---
status: draft
issue: 1432
intent: intent/2026-10-26-1432-contract-signature-verify.md
---

# Spec: verify the contract approval signature in TFactory (#1432)

## Design

**Decision (the user's):** TFactory verifies the approval signature with the
signer's public key. AIFactory forwards the signature unchanged and does not
re-sign.

Today's signature is HMAC (PFactory `apps/backend/plan/emit/signing.py:131-159`),
so it has no public key. PFactory therefore adds a second, asymmetric
signature next to the HMAC one. The HMAC path that AIFactory checks stays
byte-for-byte the same.

### 1. What is signed (canonicalisation)

The Ed25519 signature covers exactly the bytes the HMAC covers today. Both use
`_signing_bytes` (PFactory `signing.py:58-80`, the mirror of AIFactory
`trusted_plan._signing_bytes`):

```
canonical(contract minus "approval") | approved_by | approval_timestamp | plan_contract_version | kid
```

- `canonical` is `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, encoded as UTF-8.
- The whole `approval` object is left out, so adding the new field inside it
  changes neither signature.
- `kid` is the Ed25519 key id, which is separate from the HMAC kid. It is
  required for the new signature. A signature without a kid is invalid, so
  every key can be revoked. PFactory already lowercases the kid when it signs
  (`signing.py:109`), so the signed bytes carry it as signed. TFactory
  lowercases the kid only to look it up in the keyring, never inside the
  bytes it verifies.

The canonical form is whatever Python's `json.dumps(sort_keys=True,
separators=(",", ":"), ensure_ascii=False)` produces. That is not RFC 8785
(JCS): non-ASCII text is emitted as raw UTF-8 bytes, floats use Python's
`repr`, and `NaN`/`Infinity` are allowed. Both signers and the verifier are
Python today. Any non-Python signer must reproduce exactly these bytes, and
the golden vector is the test it has to pass.

TFactory keeps its own copy of `_canonical` and `_signing_bytes` (about 15
lines) in a new module. TFactory does not import from PFactory. A shared
golden vector (a fixed contract, a fixed test key pair and the expected
signature) is checked in the tests of both repos, so the two copies cannot
drift apart without a test failing.

### 2. The envelope

PFactory adds one optional field to the existing `approval` object:

```json
"approval": {
  "approved_by": "pfactory", "approval_timestamp": "...",
  "plan_contract_version": "2", "signature": "<hmac hex>", "kid": "<hmac kid>",
  "pubkey_signature": {"alg": "ed25519", "kid": "<ed kid>", "sig": "<base64>"}
}
```

The vendored schema already allows this: `approval` has
`additionalProperties: true` (TFactory `apps/backend/contracts/task-contract-v2.schema.json`).
The drift gate is directional, so it still passes. PFactory adds
`pubkey_signature` only when it also HMAC-signs (`contract_emit.py:353-362`)
and an Ed25519 key is configured.

### 3. Forwarding: AIFactory sends the signed original unchanged

`build_ingest_payload` (AIFactory `pfactory/tfactory_client.py:642-711`) adds
fields to `contract` (lines 698-709), and that breaks any signature over it.
AIFactory keeps doing that for compatibility. It also sends the contract it
had before those additions, untouched, as a new top-level field
`signed_contract`. It sends it only when that contract has an `approval`
block and the contract is not held (`held` at line 692).
AIFactory adds no signature and edits no bytes.

TFactory's `SpecIngestRequest` (`server/routes/specs.py:79`) gets an optional
`signed_contract: dict | None`, which is passed to `create_spec_ingest_workspace`.

### 4. Verification in TFactory

New module `apps/backend/agents/contract_signature.py` with
`verify_contract_signature(contract, env=None) -> list[str]`. It returns
reason codes and never values: `unsigned`, `unknown_kid`, `retired_kid`,
`bad_alg`, `bad_signature`, `malformed`. It uses
`cryptography.hazmat.primitives.asymmetric.ed25519`.

In `create_spec_ingest_workspace` (`task_control.py:503-540`), next to the
#1427 schema check and under the same marker predicate:

1. Verify `signed_contract` when it is present, else verify `contract`.
   An old AIFactory sends no `signed_contract`. Its changed `contract` then
   fails, and the failure shows in warn mode during the rollout.
2. Valid `signed_contract`: the contract that is persisted and used is
   `signed_contract`, plus the AIFactory overlay taken from `contract` with
   AIFactory's own add-only rules: `execution.phase_models` merged with
   the signed values winning, `provenance.github_issue` set only if missing,
   and `correlation_key` set only if missing. Any other difference in
   `contract` is ignored. The authoritative `tfactory` block therefore always
   comes from signed bytes. For `execution.phase_models`, a key the signed
   copy sets always keeps its signed value; the unsigned `contract` may only
   add keys the signed copy does not set. This keeps AIFactory's build-model
   routing, which it writes after PFactory signs.
3. **Repo binding.** After the signature verifies, TFactory compares the
   signed `provenance.repo` with the repo the request runs against: the
   request's `repo` and its clone URL (`git_url`, or the registered project's
   `clonedFrom` when the project already exists). Every value that is present
   must name the same `owner/name`, compared case-insensitively after
   `repo_ref.project_of`, and for a URL after dropping the host and a `.git`
   suffix. A mismatch gives the code `repo_mismatch`. A signed contract with no
   `provenance.repo`, or a request with no repo at all, gives `repo_unbound`.
   Both codes go through the signature mode below: warn logs them, enforce
   rejects.
4. **Schema check on the result.** The #1427 schema validation runs on the
   contract that is persisted, which is `signed_contract` plus the overlay
   after the swap, not on the forwarded `contract`.
5. Failure: `TFACTORY_CONTRACT_SIGNATURE_MODE`, with the same semantics as
   `TFACTORY_CONTRACT_SCHEMA_MODE`. `warn` (the default) logs the reason
   codes, appends `task contract signature invalid (warn mode): <codes>` to
   `warnings`, and keeps today's behaviour (`contract` is persisted). `enforce`,
   or any unknown value (fail closed), raises `ValueError` and the route turns
   it into a 400. It is a separate variable so the two gates can be flipped
   on their own. Enforce rejects `unsigned` as well, otherwise stripping
   `approval` would bypass the gate.
6. Contracts without RFC-0002 markers, and the MCP ingest path that sends no
   contract (`task_control.py:1039`), are not affected.

### What the signature proves, and what it does not

The signature proves who wrote the contract and its `tfactory` test profile,
and that neither changed after PFactory approved them. With the repo binding,
it also ties them to one repo. It does **not** prove:

- **Which build branch is tested.** `source_branch` and `ref` are request
  fields, outside the signed bytes.
- **The acceptance criteria.** TFactory parses them from the request's
  unsigned `spec_text` (`task_control.py:485`), not from the contract.
- **Freshness.** See the replay risk below.

### 5. Key distribution: the existing gitops env path, no new service

- **Private key (PFactory):**
  `PFACTORY_CONTRACT_SIGNING_KEY__<KID>`, the base64 of the 32-byte raw Ed25519
  private key. It lives in `factory-secrets` through agenix, the same path as
  `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY` (factory-gitops
  `apps/pfactory/manifests/manifests.yaml:283`). Only PFactory mounts it.
- **Public key (TFactory):**
  `TFACTORY_CONTRACT_PUBKEY_<AUTHORITY>__<KID>`, the base64 of the 32-byte raw
  public key. It is not secret, so it is a plain `value:` in TFactory's env
  list in factory-gitops `apps/tfactory/manifests/manifests.yaml` (next to
  `TFACTORY_AUTO_PLAN`, line 467). It is reviewable in git and needs no secret
  mount. The authority matches `approval.approved_by`, case-insensitively.
- The key pair is made once with `cryptography` (or `openssl genpkey -algorithm ed25519`),
  and only the private half goes through `agenix -e`.

### 6. Key rotation and revocation

This is the scheme `trusted_plan` already uses (#323/#401):

- PFactory's kid is resolved by the existing `key_from_env`. It gets a
  `prefix` parameter, and its pin variable becomes
  `PFACTORY_CONTRACT_SIGNING_KID`, so there are the same ambiguity errors.
- TFactory loads every `TFACTORY_CONTRACT_PUBKEY_*__*` into a keyring, so
  several keys are valid at once.
- To rotate: add the new public key to TFactory, add the new private key to
  PFactory and pin the new kid, then remove the old public key.
- To revoke: `TFACTORY_CONTRACT_RETIRED_KIDS` (comma-separated
  `authority/kid` or a bare `kid`) rejects that kid even while its key is
  still configured.

## Alternatives rejected

- **AIFactory re-signs** the changed contract with its own key. TFactory
  would then trust AIFactory's word for what PFactory approved, and AIFactory
  runs agent-written code. The user rejected it.
- **A shared HMAC** (give TFactory `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY`).
  Any holder can forge. TFactory would gain the power to mint trusted plans
  for AIFactory's skip-planning fast path, and the blast radius of
  `factory-secrets` would grow.
- **No verification** (schema only, #1427). This leaves the gap in the issue.
- **AIFactory stops changing `contract`** and moves its overlay to top-level
  fields. This is cleaner, but it changes TFactory's three readers of
  `contract.execution`, `provenance` and `correlation_key`, and it breaks a
  mixed-version rollout. The additive `signed_contract` field is backward
  compatible both ways.
- **Replace HMAC with Ed25519 everywhere.** AIFactory's fast path and CFactory's
  signer would have to move together. That is out of scope for #1432.
- **A key service or JWKS endpoint.** It is a new service to run and secure,
  for one public key that changes rarely.

## Risks

- **Canonicalisation drift** between the TFactory and PFactory copies makes
  every signature fail. The golden vector in both test suites guards against it, and
  warn mode makes the failure visible before enforce.
- **Overlay rules drift:** AIFactory adds a new field to `contract`. The field
  is dropped when `signed_contract` verifies, so the new behaviour silently
  does not reach TFactory. The TFactory test pins the overlay paths, and the
  handoff docstring names them.
- **Enforce breaks hand-posted contracts** (the #1431 proof-run template).
  This is the intent's open question, and flipping to enforce waits on it.
  This lane only ships warn mode.
- **Replay.** A valid signed contract can be sent again, with any build
  branch and any `spec_text`. The `spec_id` 409 does not limit this: the
  caller chooses `spec_id` (`specs.py:310`, checked at `task_control.py:480`),
  so a fresh id always works. The repo binding limits a replay to the repo the
  contract was approved for. Within that repo, the caller can still pair the
  signed profile with another branch or other acceptance criteria. Expiry and
  a single-use nonce are out of scope.
- **The caller picks a weak verify model (accepted).** The overlay may add
  `execution.phase_models` keys the signed copy does not set, so the ingest
  caller can route verify lanes to a weaker model. This is accepted: the
  caller is an authenticated service that already controls the unsigned
  `spec_text` and acceptance criteria, so choosing the verify model gives it
  no new power. Keys the signed copy sets cannot be changed.
- **Hosts:** TFactory and PFactory pods on the k3d cluster. There is no host-level change.

## Open questions

- Deferred until the enforce flip: do proof runs (the #1431 template) get a
  PFactory-signed contract, or do they send no contract? Warn mode does not
  need the answer.

## Verification

- TFactory unit tests (`tests/test_contract_signature.py`): valid, tampered
  `tfactory` block, unknown kid, retired kid, missing `pubkey_signature`,
  wrong alg, bad base64, the golden vector, the overlay merge, `repo_mismatch`
  and `repo_unbound`, and that the unsigned `contract` can add a
  `phase_models` key but cannot change one the signed copy sets. There are ingest tests
  in both modes, and one where the swapped contract fails the schema check.
- PFactory test: the golden vector, the HMAC envelope is byte-identical with
  and without the Ed25519 key, `key_from_env` with the new prefix, and that a
  bad Ed25519 key or kid setup logs a warning and still emits the HMAC
  signature without `pubkey_signature`.
- AIFactory test: `signed_contract` equals the stored contract when the overlay
  changed `contract`, and is absent when held or unsigned.
- After release in warn mode: a PARR run ends with a stored contract and no
  signature warning in the TFactory log. A hand-tampered ingest gets a warning.
  After the enforce flip it gets a 400.

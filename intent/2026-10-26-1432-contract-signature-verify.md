---
status: draft
issue: 1432
author: olafkfreund
predecessor: intent/2026-10-24-1427-contract-schema-gate.md
---

# Intent: verify the contract approval signature in TFactory (#1432)

## Problem

TFactory's spec ingest (`POST /api/specs/ingest`) treats a Task Contract's
`tfactory` block as the authoritative test profile and writes the contract to
`context/task_contract.json`. Since #1427 it checks the contract against the
schema. Nothing checks who approved the contract. Any caller that can reach the
ingest endpoint can send a contract that says what TFactory must test, and
TFactory trusts it.

The contract does carry an `approval` envelope today, but TFactory cannot
check it:

- PFactory signs with HMAC-SHA256 (`apps/backend/plan/emit/signing.py`). The
  key is the shared secret `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY`. Giving
  TFactory that secret would let TFactory sign plans AIFactory then trusts.
- AIFactory changes the contract before it forwards it
  (`pfactory/tfactory_client.py:build_ingest_payload`): it adds
  `execution.phase_models`, `provenance.github_issue` and `correlation_key`.
  The signature covers the whole contract, so the forwarded copy no longer
  matches it.

## Proposed outcome

- TFactory checks the approval signature on every ingested contract, using
  the signer's public key only. No TFactory pod holds a key that can sign.
- AIFactory forwards PFactory's signature unchanged. AIFactory does not
  re-sign.
- A bad, missing or revoked signature first logs a warning and shows in the
  ingest response's `warnings`. A later config flip makes it reject the
  ingest. This is the same warn-then-enforce rollout as
  `TFACTORY_CONTRACT_SCHEMA_MODE`.
- Keys can be rotated and a leaked key can be revoked without a release.

## Affected users and systems

- TFactory: spec ingest (`agents/tools_pkg/tools/task_control.py`,
  `server/routes/specs.py`).
- PFactory: contract signing (`plan/emit/signing.py`, `plan/emit/contract_emit.py`).
- AIFactory: the TFactory handoff payload (`pfactory/tfactory_client.py`).
- factory-gitops: TFactory env (the public key) and `factory-secrets` (the
  PFactory private key, through agenix).
- Operators who post contracts by hand, such as the proof-run template from #1431.

## Constraints

- AIFactory's HMAC trusted-plan fast path must keep working unchanged. The
  signing bytes and the HMAC `signature` field must not change.
- Mixed versions must keep working during the rollout. A new TFactory must
  accept an old AIFactory, and the reverse, in warn mode.
- No new service and no new dependency download. `cryptography` is already
  installed in the PFactory and TFactory images.
- Never log key material or contract values. Field paths and reason codes only (#718).

## Open questions

- In enforce mode, a contract that has no signature at all is rejected. That
  includes the hand-posted proof-run template from #1431. Proof runs then need
  a PFactory-signed contract, or must send no contract. The spec assumes this.

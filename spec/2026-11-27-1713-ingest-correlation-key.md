---
status: approved
issue: 1713
intent: intent/2026-11-27-1713-ingest-correlation-key.md
---

# Spec: a spec ingest can carry the run's correlation key

## Design

- **Request field:** `routes/specs.py` `SpecIngestRequest` (~70–165) gains
  `correlation_key: str | None = Field(default=None, max_length=128,
  pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$", description=…)`.
  - A field validator also rejects an all-digits value with a 422 (#1467).
  - The MCP-stdio proxy reuses the same model, so it inherits the field.
- **Pass-through:** `ingest_spec` (~557–567) passes `correlation_key=req.correlation_key` to
  `create_spec_ingest_workspace`.
- **Workspace:** `create_spec_ingest_workspace` (`task_control.py` ~699) gains the keyword
  `correlation_key: str | None = None`.
  - `source.json` gets `"correlation_key": _handoff_key(contract) or correlation_key`, so the
    contract's key still wins.
  - Nothing else changes. In particular, no `task_contract.json` is written when there is no
    contract.
- **Triager:** no change. `triager._correlation_key` already reads `source.json` `correlation_key`
  after the contract key and before the synthetic fallback.

## Alternatives rejected

- **A contract that carries only `correlation_key`:** `_repo_binding` treats any contract as an
  AIFactory handoff, which changes the checkout.
- **A key derived from `repo` and `pr_number`:** that isn't the key PFactory and AIFactory use for
  the run.

## Risks

- **A caller sends a wrong key:** this only mis-files the verify event in CFactory. It has no
  effect on gating.

## Verification

- **Rejected with a 422:** a key over 128 characters, one with a space, and an all-digits key.
- **Accepted with no key:** the request is accepted and `source.json` `correlation_key` is null.
- **Stored:** an ingest with `correlation_key="pf-337-x"` writes it to `source.json`, and
  `triager._correlation_key` returns it.
- **Precedence:** a contract with its own key, plus a request key, stores the contract key.
- **Checks:** the existing `tests/test_spec_ingest_workspace.py` and the specs route tests pass,
  the ratchet passes, and CI is green.

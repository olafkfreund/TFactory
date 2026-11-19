---
status: draft
issue: 1632
intent: intent/2026-11-19-1632-tf-api-db-isolation.md
---

# Spec: API-lane generator keeps DB-backed tests isolated

## Design

Extend the "API lane" block of `context_block` in frameworks/pytest/descriptor.yaml (the text injected into Gen-Functional) with a short isolation paragraph: the SUT's datastore is shared across tests and runs; make every test create its own data with unique identifiers (uuid suffix) and assert the AC's pagination, ordering and stats against that data (a tie-ordering test creates its own tied rows and asserts relative order, a stats test reads a before/after delta), never against an assumed empty or fixed dataset; clean up created rows through the SUT API in a fixture finalizer when a delete endpoint exists; do not connect to the database directly.

Decision (owner delegation 2026-11-19): prompt/template change, not a generated truncate fixture. The generated test has the HTTP target and, as far as verified, no DB handle; a truncate fixture would also destroy data the SUT or other tests own.

## Alternatives rejected

- Generate truncate / fresh-schema fixtures: needs DB credentials and DDL knowledge the test does not reliably have; risky on a shared database.
- Platform-side reset between tests: a larger feature, tied to #1582; not a small change.

## Risks

Prompt guidance is advisory; it reduces but does not eliminate shared-state failures. It must not weaken AC assertions, hence the wording above. Relative-order and delta assertions assume no concurrent writers; the guidance says so.

## Verification

tests/test_framework_registry.py (or the pytest descriptor test): the pytest context block contains the isolation guidance and still the TFACTORY_TARGET_URL contract. Descriptor validator passes.

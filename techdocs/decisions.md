# Architecture decisions

The authoritative decision log lives in
[`.agent-os/product/decisions.md`](https://github.com/olafkfreund/TFactory/blob/main/.agent-os/product/decisions.md)
(highest override priority). This page summarizes the accepted records.

## DEC-001 — Standalone product, AIFactory as the wedge

*Accepted 2026-05-30 · Product / Strategy*

TFactory is a **standalone autonomous-QA product**, with the AIFactory handover as
the warm-start wedge — not merely an AIFactory feature. The market whitespace
(spec-aligned + 5-signal + autonomous triage) is genuine and standalone-sellable;
all Horizon-3 work (AC decoupling, GTM, pricing) is scoped against this.

## DEC-002 — Security scanning is out of scope

*Accepted 2026-05-30 · Product*

TFactory does **not** generate security tests (SAST / DAST / Fuzz) — those are
delegated to dedicated security pipelines. v0.2 narrowed the product to functional
+ feature testing and replaced the inherited `Functional / SAST / DAST / Fuzz`
lanes with the modality spine `unit / browser / api / integration / mutation`. The
old security lanes were **cut**, not deferred.

## DEC-003 — Browser-first lane ordering

*Accepted 2026-05-30 · Technical / Product*

When a feature can be exercised through a browser, generate a **Browser** test;
otherwise API; otherwise Integration; **Unit only as last resort**. Mutation is
orthogonal — it strengthens whatever was generated. This is deliberately the
opposite of the industry default (Diffblue, Meta TestGen-LLM, Qodo start with unit
tests): browser-first tests exercise real user-visible behaviour and produce
reviewable evidence (screenshots / video / trace), the strongest answer to "I don't
trust an AI test until I've watched it run."

## DEC-004 — Honest v0.x version line

*Accepted 2026-05-30 · Process*

The product version line is `0.x` (not the inherited AIFactory `3.0.2`).
`release.yml` auto-cuts `v<version>` on the dev→main promotion.

## DEC-005 — Credential Broker: vault-backed cloud auth with honest egress

*Accepted 2026-05-30 · Technical*

Agents authenticate to cloud environments (GCP / AWS / Azure / K8s) via a pluggable
secrets layer (`apps/backend/tfactory_secrets/`): a `SecretsBackend` abstraction +
factory + ref routing, a `CredentialBroker` extending `core/mcp_credentials.py` with
a vault-fetch head, and an explicit per-project egress opt-in (`.tfactory.yml`
`egress.enabled`, default **OFF**) with a secret-free egress manifest. v1 is
pass-through (resolve → inject → wipe); short-lived / workload-identity federation
and test-sandbox injection are fast-follows. Secrets never live in the repo;
ephemeral `0600` cred files are wiped per task.

---

> See also the product source-of-truth under `.agent-os/product/`: `mission.md`
> (positioning), `roadmap.md` (3 horizons), `pricing.md` (GTM).

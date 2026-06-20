# Vendored Factory coding-standards baseline

This directory is a **pinned, vendored copy** of the fleet's shared lint baseline.
The single source of truth lives in the Factory hub repo under `standards/`
(`coding-standards.md`, `ruff.toml`, `mypy.ini`, `.editorconfig`).

`PINNED_SHA` records the hub commit these files were copied from. A drift gate
(or a manual re-vendor) keeps this copy in sync; per the standard, a service may
only **TIGHTEN** these configs, never loosen them.

## Files

| File | What it is |
|---|---|
| `ruff.toml` | Shared Python lint baseline (explicit select set). |
| `mypy.ini` | Shared `mypy --strict` baseline. |
| `.editorconfig` | Editor baseline (also copied to the repo root). |
| `PINNED_SHA` | Hub commit these copies were vendored from. |

## How TFactory consumes it

This is **Phase 0** of adoption (Factory#154, issues #449 / #452):

- The baseline is vendored here at a pinned hub SHA.
- A **ratchet** CI job (`.github/workflows/ratchet.yml`) runs `ruff` and `mypy`
  against this baseline **only on the Python files a PR changes** (diff-scoped,
  per standards section 4.6). It is blocking, but it does not flip the whole
  legacy tree red.
- The repo-wide `ruff format --check` is also added (formatting is auto-applied,
  never reviewed — standards section 3.6).

Whole-repo strict `ruff check` / `mypy --strict` are intentionally **not** made
blocking yet: the legacy violation count would make CI instantly red. Those
become blocking incrementally as the ratchet cleans files on touch.

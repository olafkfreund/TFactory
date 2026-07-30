# Vendored Factory coding-standards baseline

This directory is a **pinned, vendored copy** of the fleet's shared lint baseline.
The single source of truth lives in the Factory hub repo under `standards/`
(`coding-standards.md`, `ruff.toml`, `mypy.ini`, `.editorconfig`).

`.hub-sha` records the hub commit these files were copied from. It is the one
pin filename fleet-wide (hub `standards/README.md`), so tooling reads the same
path in every service with no per-repo special case. This directory used
`PINNED_SHA` until Factory#434.

A blocking drift gate (`.github/workflows/ratchet.yml`, job `shared-baseline
drift gate`) diffs every file below against the hub at that SHA on each PR, so
this copy cannot silently fork. Per the standard, a service may only **TIGHTEN**
these configs, never loosen them — and tighten-only overrides live in the repo's
root `ruff.toml`, never in this directory.

## Files

| File | What it is | Compared how |
|---|---|---|
| `coding-standards.md` | The normative standard (Python, TypeScript, cross-cutting, CI). Not editable here — change it in the hub. | **byte-exact** |
| `ruff.toml` | Shared Python lint baseline (explicit select set). | body only |
| `mypy.ini` | Shared `mypy --strict` baseline. | body only |
| `.editorconfig` | Editor baseline (also copied to the repo root). | body only |
| `.hub-sha` | Hub commit these copies were vendored from. | not compared — it *is* the pin |

`coding-standards.md` is compared byte-exact because the body-only comparator
strips lines starting with `#`, which in Markdown is every heading (58 of its
198 lines); a stripped compare would let whole section titles drift unnoticed.

## Re-vendoring after a hub change

```sh
HUB=<hub commit sha on Factory main>
for f in ruff.toml mypy.ini .editorconfig coding-standards.md; do
  git -C ../Factory show "$HUB:standards/$f" > "standards/$f"
done
printf '%s\n' "$HUB" > standards/.hub-sha
```

The pin must name a commit on the hub's `main`. Factory squash-merges, so a
PR-head SHA never survives to `main` and a pin pointing at one will fetch-fail
once the branch is deleted.

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

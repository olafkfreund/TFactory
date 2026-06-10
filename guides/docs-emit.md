# Test-result docs emit (verify → docs) — #341

TFactory publishes a durable **test-result doc** for each run through the same
docs-emit core PFactory uses for plan docs, keyed by the **same
`correlation_key`** as the plan it verifies. That closes the PARR doc trail:

```
plan (PFactory) → code (AIFactory) → verify (TFactory)
```

A consumer that has a plan's `correlation_key` can resolve the test-result doc
for the same key — the verify side of the cross-factory memory.

## What ships

- `apps/backend/emit/docs/` — the plan-agnostic core vendored from PFactory
  (`bundle`, `targets/{base,repo,backstage,confluence,registry,github_writer}`,
  `resolve.PlanDocsResolver`, `emit_docs.emit_bundle`). Duplicate-then-converge:
  if the two copies drift, lift the core into a shared package both vendor.
- `emit/docs/render_test_results.py` — TFactory's only new code:
  `render_test_results(triage, *, correlation_key, spec_id, component_ref=None)
  -> DocBundle`. Pure (no clock/fs/network); `generated_by="tfactory"`.
- `agents/docs_emit_trigger.py::maybe_emit_docs` — the Triager terminal-status
  hook. Renders `findings/triage_report.json`, resolves the correlation key with
  the Triager's canonical precedence (#249: contract key → issue # → `tf-<id>`),
  and publishes via `emit_bundle`.

## Enabling it

Off by default. Opt in:

| Env | Effect |
|---|---|
| `TFACTORY_DOCS_EMIT=1` | Master switch — render + publish on terminal status |
| `TFACTORY_DOCS_DIR=<dir>` | Repo-target output dir (default `~/.tfactory/test-docs`) |
| `TFACTORY_DOCS_GIT_WRITE=1` | Let remote targets commit (else dry-run) |
| `TFACTORY_DOCS_BACKSTAGE=1` / `BACKSTAGE_BASE_URL` | Add the Backstage target |
| `TFACTORY_DOCS_CONFLUENCE=1` / `CONFLUENCE_BASE_URL` | Add the Confluence target |

The repo/directory target is always included; it writes `<spec_id>-tests.md`,
upserts `registry.json` (keyed by `correlation_key`) and regenerates `index.md`.
Best-effort throughout: any failure is logged and swallowed — it never breaks a
run.

## Resolving a doc

```python
from emit.docs import PlanDocsResolver
resolver = PlanDocsResolver.from_dir("~/.tfactory/test-docs")
entry = resolver.resolve("<correlation_key>")  # → {doc_file, accept_rate, …}
```

## Not yet (follow-up)

- **Settings-UX reuse (AC5).** The `connections_to_targets` seam is vendored and
  ready, but the web-server `routes/docs_targets.py` + the `DocsTargetConnection`
  model + the `DocsTargetsSettings.tsx` panel are not yet grafted from PFactory.
  Until then, target selection is env-driven (table above).

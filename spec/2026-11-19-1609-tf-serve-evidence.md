---
status: draft
issue: 1609
intent: intent/2026-11-19-1609-tf-serve-evidence.md
---

# Spec: Infer serve targets from the spec text

## Design

nix_env.py adds `_spec_text_targets(spec_dir, project_dir) -> list[str] | None`: read context/aifactory_spec.md, regex repo-relative tokens `[\w][\w./-]*\.(js|mjs|cjs|ts|html?)` (plain components only), keep those that exist as files under project_dir, dedupe, cap 20. run_browser_evidence uses it only when source.json has no target_paths. detect_serve_command is unchanged, so the existing node-subdir rule then serves `PORT=<port> node scoreboard/server.js`. serve_command in the evidence is the command the Job ran, so it is correct once the right one is chosen.

## Alternatives rejected

Always preferring a node entry when the repo has one: breaks python-only specs. Parsing the PR diff: no PR file list is available in ingest.

## Risks

A spec that mentions an unrelated existing file could steer detection; limited to files that exist in the checkout and only applied when no explicit targets were given. Startup success is not proven by this change (readiness loop unchanged).

## Verification

tests/test_serve_command_node_subdir.py: demo layout with spec text naming scoreboard/server.js and no target_paths yields the node command; spec text without paths yields the old behaviour; nonexistent and traversal paths are ignored. The first fails on current dev.

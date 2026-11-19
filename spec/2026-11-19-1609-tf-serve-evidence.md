---
status: approved
issue: 1609
intent: intent/2026-11-19-1609-tf-serve-evidence.md
---

# Spec: Infer serve targets from the spec text

## Design

nix_env.py adds \`_spec_text_targets(spec_dir, project_dir) -> list[str]\`: read context/aifactory_spec.md and match whole repo-relative tokens with the regex \`(?<![\w./~-])((?:[\w][\w.-]*/)*[\w][\w.-]*\.(?:js|mjs|cjs|ts))(?![\w-]|\.\w|/)\`, keep those that exist as files whose resolved path stays inside project_dir, dedupe, cap 20. Only script suffixes are inferred, so an inferred html page cannot flip detection into the static-page branch. A new \`_browser_targets(spec_dir, project_dir)\` returns source.json target_paths when non-empty, else the inferred list or None; run_browser_evidence calls it in place of its inline source.json read. detect_serve_command is unchanged, so its node-subdir rule serves \`PORT=<port> node scoreboard/server.js\`; serve_command in the evidence is the command the Job ran, so it is correct once the right one is chosen.

## Alternatives rejected

Always preferring a node entry when the repo has one: breaks python-only specs. Parsing the PR diff: no PR file list is available in ingest.

## Risks

A spec that mentions an unrelated existing file could steer detection; limited to files that exist in the checkout and only applied when no explicit targets were given. Startup success is not proven by this change (readiness loop unchanged).

## Verification

tests/test_serve_command_node_subdir.py: (1) _browser_targets with no source targets and spec text naming scoreboard/server.js and scoreboard/index.html returns only the .js, and detect_serve_command with it gives the node command; (2) explicit source.json targets take precedence; (3) spec text with no paths, a nonexistent file, ../scoreboard/server.js, /scoreboard/server.js, scoreboard/server.json: none inferred. Fixtures contain "listen" so removing a path guard breaks them. (1) fails on current dev.

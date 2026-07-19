---
layout: default
title: Environment Reference
permalink: /environment-reference/
nav_order: 5.8
---

# Environment Variable Reference

<div class="reveal" markdown="1">

Every environment variable the TFactory backend reads, in one place. A fresh
operator installing TFactory from scratch should be able to find each required
credential, each pipeline gate, each feature flag, and each tuning knob here —
with its default, whether it is required, what it does, and where it is read.

This reference covers the **backend service** (`apps/backend`). The web-server
portal has its own `apps/web-server/.env.example`; the handful of `APP_*`
portal variables are noted at the end under
[Portal variables](#portal-variables-web-server).

</div>

## How to set variables

- **Local dev (docker-compose):** copy `.env.example` to `.env` next to
  `docker-compose.yml` and uncomment the values you need.
- **Live cluster (gitops / Helm):** variables are set on the TFactory
  control-plane Deployment. In the gitops repo these live under
  `apps/tfactory/manifests`; the Helm chart exposes the equivalent values.
  Note that variables consumed by dispatched verify Jobs (the Nix runner and
  credential-injection groups) do **not** inherit automatically from the
  control-plane pod — TFactory forwards a specific allow-list into each Job
  (see [Verify-Job credential injection](#verify-job-credential-injection)).
- **Booleans** are truthy when set to one of `1`, `true`, `yes`, `on`
  (case-insensitive) unless noted otherwise. "Default ON" means the flag is
  active unless you explicitly set a falsy value; "Default OFF" means it does
  nothing until you opt in.
- `[write]` marks a flag that causes a **real side effect** (a git commit, a PR
  comment, a webhook POST, a handoff to another service). These are dry-run or
  off by default per the project's "no automatic side-effects" policy.

---

## Pipeline auto-fire gates

Each pipeline stage auto-advances to the next when its gate is `1` (the
default). Pin a gate to `0` to stop the pipeline before that stage so you can
inspect the intermediate output. Test fixtures set these to `0`.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_AUTO_PLAN` | `1` | No | Default ON | Planner auto-fires from task creation. Set `0` to stop after intake. | `agents/planner.py` |
| `TFACTORY_AUTO_GENERATE` | `1` | No | Default ON | Gen-Functional auto-fires from the Planner. Set `0` to stop after planning. | `agents/gen_functional.py` |
| `TFACTORY_AUTO_EVALUATE` | `1` | No | Default ON | Evaluator auto-fires from Gen-Functional. Set `0` to stop after generation. | `agents/evaluator.py` |
| `TFACTORY_AUTO_TRIAGE` | `1` | No | Default ON | Triager auto-fires from the Evaluator. Set `0` to stop after evaluation. | `agents/triager.py` |
| `TFACTORY_REVIEW_LANE` | `0` | No | Default OFF | Enable the additive adversarial review lane (writes `findings/review.json`; never blocks the verdict). Set `1` to enable. | `agents/review_lane.py` |

---

## Triager side-effects

The Triager can write back to git and to GitHub. All write paths default to
**dry-run** except template harvest, which writes low-risk local files.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_TRIAGER_GIT_WRITE` | off (dry-run) | No | Default OFF `[write]` | Commit accepted tests to the feature branch. Set `1` to actually commit. | `agents/triager.py`, `tools/git_writer.py`, `integrations/pfactory/run.py` |
| `TFACTORY_TRIAGER_PR_COMMENT` | off (dry-run) | No | Default OFF `[write]` | Post the triage report via `gh pr comment`. Set `1` to actually post. | `agents/triager.py`, `integrations/pfactory/run.py` |
| `TFACTORY_PR_STATUS` | off (dry-run) | No | Default OFF `[write]` | Publish the quality-gate commit status on the PR. Set `1` to actually publish. | `agents/triager.py` |
| `TFACTORY_TRIAGER_GIT_SIGN` | off | No | Default OFF | GPG-sign the commits made by the git-writer. Opt in with `1`. | `tools/git_writer.py` |
| `TFACTORY_TRIAGER_HARVEST` | on | No | Default ON `[write]` | Promote high-confidence accepts into the project template library at `<project>/.tfactory/templates/`. Set `0` to skip. | `agents/triager.py` |
| `TFACTORY_TRIAGER_HARVEST_GLOBAL` | off | No | Default OFF `[write]` | Also write harvested templates to the cross-project global library at `~/.tfactory/templates/`. Opt in with `1`. | `agents/triager.py` |

---

## Completion and stage events

Best-effort notifications emitted when a run reaches a terminal status or when
a stage transitions. All are OFF by default; a missing or failing target never
affects the run.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_COMPLETION_WEBHOOK` | unset | No | Default OFF `[write]` | URL POSTed with the completion envelope when a run completes. | `agents/triager.py`, `agents/completion_outbox.py` |
| `TFACTORY_COMPLETION_WEBHOOK_TIMEOUT` | `5` | No | — | Timeout (seconds) for the completion webhook POST. | `agents/triager.py`, `agents/completion_outbox.py` |
| `TFACTORY_COMPLETION_SENTINEL` | off | No | Default OFF | Write `findings/COMPLETED.json` on completion for a same-host watcher to stat. | `agents/triager.py` |
| `TFACTORY_COMPLETION_OUTBOX` | off | No | Default OFF | Enable the durable completion-event outbox (retries the webhook with backoff). | `agents/completion_outbox.py` |
| `TFACTORY_COMPLETION_OUTBOX_BACKOFF_BASE` | `5.0` | No | — | Outbox retry backoff base (seconds). | `agents/completion_outbox.py` |
| `TFACTORY_COMPLETION_OUTBOX_BACKOFF_CAP` | `3600.0` | No | — | Outbox retry backoff ceiling (seconds). | `agents/completion_outbox.py` |
| `TFACTORY_COMPLETION_OUTBOX_MAX_ATTEMPTS` | `20` | No | — | Maximum outbox delivery attempts before giving up. | `agents/completion_outbox.py` |
| `TFACTORY_STAGE_EVENT_SENTINEL` | off | No | Default OFF | Write a per-stage sentinel file on each stage transition. | `agents/stage_events.py` |
| `TFACTORY_STAGE_EVENT_WEBHOOK` | unset | No | Default OFF `[write]` | URL POSTed on each stage transition. | `agents/stage_events.py` |
| `TFACTORY_STAGE_EVENT_WEBHOOK_TIMEOUT` | `5` | No | — | Timeout (seconds) for the stage-event webhook POST. | `agents/stage_events.py` |
| `TFACTORY_EVENT_SOURCE` | derived | No | — | Override the CloudEvents `source` field in the completion envelope. | `agents/completion_envelope.py` |
| `TRACEPARENT` | unset | No | — | W3C trace-context parent, inherited into the completion envelope for distributed tracing. Normally injected by the caller. | `agents/completion_envelope.py` |
| `TRACESTATE` | unset | No | — | W3C trace-context state, inherited into the completion envelope. Normally injected by the caller. | `agents/completion_envelope.py` |

---

## Handback loop

When verification fails, TFactory can prepare and (optionally) send a
correction bundle back to AIFactory, then re-verify, up to a cycle cap.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_HANDBACK_PREPARE` | on | No | Default ON | Prepare the handback bundle on a failing verdict. Disable with a falsy value. | `agents/handback/trigger.py` |
| `TFACTORY_HANDBACK_SEND` | off | No | Default OFF `[write]` | Actually send the handback to AIFactory (triggers a re-build). Opt in with `1`. | `agents/handback/trigger.py` |
| `TFACTORY_HANDBACK_MAX_CYCLES` | `2` | No | — | Correction-cycle cap for the handback loop. Values `<= 0` fall back to the default. | `agents/handback/loop.py` |

---

## VAL-3 disposable target

VAL-3 is the highest verification-assurance level: tests run against a real,
disposable deploy target. These flags select which target shape TFactory
provisions. They are read from the run environment.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_VAL3_LOCAL_VM` | off | No | Default OFF | Provision a local VM as the disposable target. | `agents/disposable_target.py` |
| `TFACTORY_VAL3_K8S_JOB` | off | No | Default OFF | Provision a Kubernetes Job as the disposable target (see `agents/k8s_job_target.py`). | `agents/disposable_target.py` |
| `TFACTORY_VAL3_CLOUD` | unset | No | Default OFF | Provision a cloud target; value names the cloud/target profile. | `agents/disposable_target.py` |
| `TFACTORY_VAL3_TARGET_IS_PROD` | off | No | Default OFF | Assert the target is production (guardrail flag; also inferred from the contract). | `agents/disposable_target.py` |
| `TFACTORY_TARGET_URL` | injected | No | — | The live target URL, **set by TFactory** into the test runtime (first `wait_for` URL) and read by the runner's network guard. Operators do not set this. | `tools/runners/lane_dispatch.py`, `tools/runners/net_guard.py` |

---

## Nix runner and verify backend

Select how the pytest/verify lane executes: the legacy host/docker runner, or
the per-task Nix Kubernetes Job (RFC-0005 / RFC-0016). See
`docs/nix-reproducible-testing.md` for the full picture.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_VERIFY_BACKEND` | auto | No | — | Force the verify lane: `nixjob` (per-task Nix Job), `docker`, or `host`. Empty = auto (nixjob when a Nix image is configured and the contract declares a nix env). | `agents/evaluator.py` |
| `TFACTORY_RUNNER_MODE` | auto | No | — | Legacy-runner mode: `host`, `docker`, or empty (auto: host when no container runtime is available). | `agents/evaluator.py` |
| `TFACTORY_NIX_RUNNER_IMAGE` | unset | Required for Nix lane | — | Container image for the per-task Nix verify Job. Without it, the Nix lane is unavailable and the lane falls back to host/docker. | `agents/evaluator.py`, `agents/nix_env.py`, `agents/verify_dispatch.py` |
| `TFACTORY_NIX_IN_IMAGE` | off | No | Default OFF | The Nix store is baked into the runner image (node-agnostic); skip mounting the Nix store PVC. | `agents/nix_env.py`, `agents/verify_dispatch.py` |
| `TFACTORY_NIX_STORE_PVC` | unset | No | — | PVC name holding the shared Nix store (ignored when `TFACTORY_NIX_IN_IMAGE` is set). | `agents/nix_env.py`, `agents/verify_dispatch.py` |
| `TFACTORY_WORKSPACES_PVC` | unset | No | — | PVC name for the shared workspaces / data root mounted into the Nix Job. | `agents/nix_env.py`, `agents/verify_dispatch.py` |
| `TFACTORY_SANDBOX_NAMESPACE` | `factory` | No | — | Kubernetes namespace the Nix/verify Jobs are created in. | `agents/nix_env.py`, `agents/verify_dispatch.py` |
| `TFACTORY_DATA_ROOT` | unset | No | — | Data-root path used to derive the Nix Job's co-mount subPath. | `agents/nix_env.py` |
| `TFACTORY_VERIFY_EXEC` | `inpod` | No | — | Verify execution mode: `kubejob` dispatches a separate verify Job; anything else runs in-pod. | `agents/verify_dispatch.py` |
| `TFACTORY_EQUIVALENCE_LANE` | off | No | Default OFF | Enable the source-vs-port equivalence lane. Truthy to enable. | `agents/evaluator.py` |
| `TFACTORY_EQUIVALENCE_BACKEND` | `docker` | No | — | Equivalence-lane backend (`docker`, or a Nix backend). | `agents/equivalence_lane.py` |
| `TFACTORY_EQUIVALENCE_IMAGE` | `tfactory-runner-nix:latest` / `tfactory-runner-pytest:latest` | No | — | Image for the equivalence lane (default depends on backend). | `agents/equivalence_lane.py` |
| `TFACTORY_VERDICT_VOTES` | `3` | No | — | Best-of-N independent evaluation passes for the verdict. | `agents/evaluator.py` |
| `TFACTORY_CI_PARITY` | `1` | No | Default ON | Apply CI-parity defaults in the docker runner. Set `0` to disable. | `tools/runners/docker_runner.py` |
| `TFACTORY_CONTAINER_BIN` | `docker` | No | — | Container CLI used by the docker runner and evaluator (`docker` or `podman`). | `tools/runners/docker_runner.py`, `agents/evaluator.py` |

---

## Verify-Job credential injection

When `TFACTORY_VERIFY_EXEC=kubejob`, TFactory builds a separate Kubernetes Job
and forwards a specific allow-list of provider/config variables into it (always
as env, never as argv). These control the Job image and how secrets are
sourced. Provider credentials (`OPENAI_API_KEY`, `GITHUB_TOKEN`, etc.) are
forwarded from the pod env, or sourced via `secretKeyRef` when a provider
Secret is named.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_VERIFY_IMAGE` | fallback image | No | — | Explicit image for the verify Job (overrides the running image). | `agents/verify_dispatch.py` |
| `TFACTORY_IMAGE` | unset | No | — | The currently-running control-plane image, reused for the verify Job when no explicit verify image is set. | `agents/verify_dispatch.py` |
| `APP_BACKEND_PATH` | `/home/projects/MagesticAI/apps/backend` | No | — | Backend path baked into the verify Job's `PYTHONPATH` (web-server sibling derived from it). | `agents/verify_dispatch.py` |
| `TFACTORY_VERIFY_OAUTH_SECRET_NAME` | unset | No | — | Kubernetes Secret name holding the Claude OAuth token for the verify Job. | `agents/verify_dispatch.py` |
| `TFACTORY_VERIFY_OAUTH_SECRET_KEY` | `oauth-token` | No | — | Key within the OAuth Secret. | `agents/verify_dispatch.py` |
| `TFACTORY_VERIFY_PROVIDER_SECRET_NAME` | unset | No | — | Secret name from which non-Claude provider keys are sourced via `secretKeyRef` (key = lower-kebab of the var, e.g. `OPENAI_API_KEY` -> `openai-api-key`). When unset, keys forward as resolved pod-env values. | `agents/verify_dispatch.py` |
| `TFACTORY_VERIFY_CLI_CREDS_SECRET` | unset | No | — | Secret name holding CLI credentials mounted into the verify Job. | `agents/verify_dispatch.py` |
| `DATABASE_URL` | unset | No | — | Database URL forwarded into the verify Job when the pipeline's terminal store write needs it. | `agents/verify_dispatch.py` |

> Note: `ANTHROPIC_API_KEY` is intentionally **never** forwarded into the verify
> Job — this prevents silent API billing when OAuth is the intended auth path.

---

## Infrastructure, paths, and auth

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_WORKSPACE_ROOT` | `~/.tfactory` | No | — | Root the portal endpoints and agents read/write workspaces from. | `agents/liveness_sweep.py`, `agents/completion_outbox.py`, `agents/handback/rerun.py`, `agents/tools_pkg/tools/task_control.py` |
| `TFACTORY_DATA_ROOT` | unset | No | — | See [Nix runner](#nix-runner-and-verify-backend). | `agents/nix_env.py` |
| `TFACTORY_SPEC_DIR` | unset | No | — | Spec directory the MCP server operates on. | `mcp_server/tfactory_server.py` |
| `TFACTORY_PROJECT_DIR` | `CLAUDE_PROJECT_DIR` | No | — | Project directory for the MCP server (falls back to `CLAUDE_PROJECT_DIR`). | `mcp_server/tfactory_server.py` |
| `CLAUDE_PROJECT_DIR` | unset | No | — | Project dir set by Claude Code; used as the `TFACTORY_PROJECT_DIR` fallback. | `mcp_server/tfactory_server.py` |
| `TFACTORY_API_URL` | built-in default | No | — | Base URL of the TFactory HTTP API the agent tools call. | `agents/tools_pkg/http_client.py` |
| `TFACTORY_SELF_API_URL` | `http://localhost:3103` | No | — | TFactory's own API base URL used by the handback sender. | `agents/handback/send.py` |
| `TFACTORY_API_TOKEN_FILE` | built-in default | No | — | Path to the file holding the TFactory API token. | `agents/tools_pkg/http_client.py` |
| `TFACTORY_MCP_KEY` | unset | No | — | API key sent by the agent HTTP client to the TFactory API/MCP. | `agents/tools_pkg/http_client.py` |
| `TFACTORY_PORTAL_PORT` | `3103` | No | — | Portal port used when building portal task-detail links. | `agents/tools_pkg/tools/task_control.py` |
| `TFACTORY_AIFACTORY_ROOT` | unset | No | — | Local path to the AIFactory checkout the snapshotter operates on. | `workspaces/snapshotter.py` |
| `TFACTORY_AIFACTORY_API_URL` | built-in default | No | — | AIFactory API base URL used by the snapshotter. | `workspaces/snapshotter.py` |
| `TFACTORY_CLOUD_ASSESSMENT_ROOT` | derived | No | — | Override the root path for stored cloud-assessment artifacts. | `agents/cloud/store.py` |
| `TFACTORY_VISUAL_INSPECTION_ROOT` | derived | No | — | Override the root path for stored visual-inspection artifacts. | `agents/visual_inspection/store.py` |
| `TFACTORY_STALL_DEADLINE_SECONDS` | `900` | No | — | Idle budget before an active stage is considered stalled (liveness). | `agents/liveness.py` |
| `TFACTORY_DEP_AGE_CHECK` | `1` | No | Default ON | Dependency freshness/age check in the dependency-review gate. Set `0` to disable. | `agents/dependency_review.py` |
| `TFACTORY_EGRESS_ENABLED` | off | No | Default OFF | Allow network egress from the secrets/egress guard. Truthy to enable. | `tfactory_secrets/egress.py` |
| `TFACTORY_BATCH_MIN_JOBS` | `2` | No | — | Minimum jobs before insight-extraction batches. | `analysis/insight_extractor.py` |
| `TFACTORY_BATCH_TIMEOUT` | `120` | No | — | Insight-extraction batch flush timeout (seconds). | `analysis/insight_extractor.py` |
| `TFACTORY_BATCH_DISABLE` | off | No | Default OFF | Disable batched insight extraction. Truthy to disable. | `analysis/insight_extractor.py` |
| `KUBECONFIG` | in-cluster | No | — | Kubeconfig for the k8s evaluator target (falls back to in-cluster config). | `agents/evaluator_targets.py` |
| `DEFAULT_BRANCH` | repo default | No | — | Default branch used by worktree/workspace git operations. | `core/worktree.py`, `cli/workspace_commands.py` |
| `AIFACTORY_BASH_SANDBOX` | `true` | No | Default ON | OS-level bash sandbox (bubblewrap) for agent bash commands. Set falsy on k3d/Kind clusters where bwrap cannot mount `/proc`. | `core/client.py` |
| `VAULT_ADDR` | unset | Required for Vault backend | — | HashiCorp Vault address for the Vault secrets backend. | `tfactory_secrets/backends/vault.py` |
| `VAULT_TOKEN` | unset | Required for Vault backend | — | Vault token for the Vault secrets backend. | `tfactory_secrets/backends/vault.py` |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | unset | Required for AWS Secrets backend | — | AWS region for the AWS Secrets Manager backend. | `tfactory_secrets/backends/aws_secrets_manager.py` |

---

## Backstage integration

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `BACKSTAGE_BASE_URL` | unset | No | — | Backstage base URL; also enables the Backstage docs target when set. | `emit/docs/targets/backstage.py`, `emit/docs/emit_docs.py` |
| `TFACTORY_BACKSTAGE_TECHINSIGHTS_URL` | unset | No | — | Backstage TechInsights endpoint for publishing verification facts. | `agents/backstage_integration.py` |
| `TFACTORY_BACKSTAGE_TOKEN` | unset | No | — | Auth token for Backstage TechInsights. | `agents/backstage_integration.py` |
| `TFACTORY_BACKSTAGE_COMPONENT` | derived | No | — | Override the Backstage component ref facts are attached to. | `agents/backstage_integration.py` |

---

## Docs emission

Controls where TFactory publishes the docs it generates.

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `TFACTORY_DOCS_DIR` | derived | No | — | Override the local output directory for emitted docs. | `emit/docs/emit_docs.py` |
| `TFACTORY_DOCS_BACKSTAGE` | off | No | Default OFF | Enable the Backstage TechDocs emission target (also on when `BACKSTAGE_BASE_URL` is set). | `emit/docs/emit_docs.py` |
| `TFACTORY_DOCS_CONFLUENCE` | off | No | Default OFF | Enable the Confluence emission target (also on when `CONFLUENCE_BASE_URL` is set). | `emit/docs/emit_docs.py` |
| `CONFLUENCE_BASE_URL` | unset | Required for Confluence target | — | Confluence base URL. | `emit/docs/targets/confluence.py` |
| `CONFLUENCE_API_TOKEN` | unset | Required for Confluence target | — | Confluence API token. | `emit/docs/targets/confluence.py` |
| `CONFLUENCE_SPACE` | unset | Required for Confluence target | — | Confluence space key. | `emit/docs/targets/confluence.py` |

---

## Providers and credentials

At least one LLM provider must be configured for the pipeline to run. Claude
via OAuth (`CLAUDE_CODE_OAUTH_TOKEN`) is the primary path; `ANTHROPIC_API_KEY`
is a fallback (and is never forwarded to verify Jobs, to avoid silent billing).

### Claude / Anthropic

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | unset | One provider required | Claude Code OAuth token (preferred auth). Also set via the UI OAuth flow. | `core/auth.py`, `core/client.py`, `core/simple_client.py`, `scripts/sdk_hello.py` |
| `ANTHROPIC_AUTH_TOKEN` | unset | No | CCR/proxy token for enterprise setups; passed through to the SDK. | `core/auth.py`, `agents/verify_dispatch.py` |
| `ANTHROPIC_API_KEY` | unset | Fallback | Anthropic API key (fallback when OAuth is absent). Never forwarded to verify Jobs. | `integrations/graphiti/config.py`, `runners/changelog_runner.py` |
| `ANTHROPIC_BASE_URL` | unset | No | Custom Anthropic/SDK endpoint. | `cli/utils.py`, `core/auth.py` (SDK passthrough) |
| `ANTHROPIC_MODEL` | unset | No | Model override passed to the SDK. | `core/auth.py`, `agents/verify_dispatch.py` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | unset | No | Maps the `haiku` shorthand to a concrete model ID. | `phase_config.py`, `core/auth.py` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | unset | No | Maps the `sonnet` shorthand to a concrete model ID. | `phase_config.py`, `core/auth.py` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | unset | No | Maps the `opus` / `opus-1m` shorthands to a concrete model ID. | `phase_config.py`, `core/auth.py` |

### SDK runtime knobs (passed through to the Claude agent subprocess)

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `NO_PROXY` | unset | No | Standard no-proxy list, forwarded to the SDK subprocess. | `core/auth.py` |
| `DISABLE_TELEMETRY` | unset | No | Disable SDK telemetry, forwarded to the SDK subprocess. | `core/auth.py` |
| `DISABLE_COST_WARNINGS` | unset | No | Suppress SDK cost warnings, forwarded to the SDK subprocess. | `core/auth.py` |
| `API_TIMEOUT_MS` | unset | No | SDK API timeout (ms), forwarded to the SDK subprocess. | `core/auth.py` |

### OpenAI and OpenAI-compatible

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `OPENAI_API_KEY` | unset | One provider required | OpenAI API key. | `phase_config.py`, `integrations/graphiti/config.py`, `runners/changelog_runner.py` |
| `OPENAI_MODEL` | `gpt-5-mini` | No | OpenAI model for the Graphiti knowledge graph. | `integrations/graphiti/config.py` |
| `OPENAI_COMPATIBLE_API_KEY` | unset | No | API key for an OpenAI-compatible endpoint. | `phase_config.py`, `providers/ollama_cloud_check.py` |
| `OPENAI_COMPATIBLE_BASE_URL` | unset | No | Base URL for an OpenAI-compatible endpoint. | `phase_config.py`, `providers/ollama_cloud_check.py` |
| `OPENAI_COMPATIBLE_MAX_TOKENS` | unset | No | Max-tokens override for the OpenAI-compatible agentic provider. | `providers/openai_compatible_agentic.py` |
| `OPENAI_COMPATIBLE_REASONING_EFFORT` | unset | No | Reasoning-effort override for the OpenAI-compatible agentic provider. | `providers/openai_compatible_agentic.py` |

### Google / Gemini

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `GOOGLE_API_KEY` | unset | One provider required | Google API key (also used as a Gemini key). | `phase_config.py`, `integrations/graphiti/config.py` |
| `GEMINI_API_KEY` | unset | No | Gemini API key (alternative to `GOOGLE_API_KEY`). | `phase_config.py` |
| `GOOGLE_LLM_MODEL` | `gemini-2.0-flash` | No | Google LLM model for Graphiti. | `integrations/graphiti/config.py` |

### Azure OpenAI

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `AZURE_OPENAI_API_KEY` | unset | No | Azure OpenAI API key. | `integrations/graphiti/config.py` |
| `AZURE_OPENAI_BASE_URL` | unset | No | Azure OpenAI base URL. | `integrations/graphiti/config.py` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | unset | No | Azure OpenAI LLM deployment name. | `integrations/graphiti/config.py` |

### Ollama (local and cloud)

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `OLLAMA_BASE_URL` | built-in default | No | Ollama server base URL. | `integrations/graphiti/config.py` |
| `OLLAMA_CLOUD_BASE_URL` | unset | No | Ollama Cloud base URL (cloud detection). | `providers/ollama_cloud_check.py` |
| `OLLAMA_API_KEY` | unset | No | Ollama (cloud) API key. | `providers/ollama_cloud_check.py` |
| `OLLAMA_LLM_MODEL` | unset | No | Ollama LLM model for Graphiti. | `integrations/graphiti/config.py` |
| `OLLAMA_EMBEDDING_MODEL` | unset | No | Ollama embedding model for Graphiti. | `integrations/graphiti/config.py` |
| `OLLAMA_EMBEDDING_DIM` | `0` | No | Ollama embedding dimension for Graphiti. | `integrations/graphiti/config.py` |

### Other providers

| Variable | Default | Required | Purpose | Read in |
|---|---|---|---|---|
| `OPENROUTER_API_KEY` | unset | No | OpenRouter API key (Graphiti). | `integrations/graphiti/config.py` |
| `VOYAGE_API_KEY` | unset | No | Voyage AI embeddings key (Graphiti). | `integrations/graphiti/config.py` |
| `VOYAGE_EMBEDDING_MODEL` | `voyage-3` | No | Voyage embedding model. | `integrations/graphiti/config.py` |
| `GITHUB_TOKEN` | unset | No | GitHub token for GitHub Models, Copilot dispatch, and GitLab provider ops. | `phase_config.py`, `agents/copilot_dispatch.py`, `agents/verify_dispatch.py` |
| `GITHUB_MODELS_DEFAULT` | `openai/gpt-4.1` | No | Default model when using the GitHub Models provider. | `phase_config.py` |
| `QA_LLM_PROVIDER` | unset | No | Force a specific provider for the QA/verify phase. | `phase_config.py`, `agents/verify_dispatch.py` |
| `AUTO_BUILD_MODEL` | unset | No | Model for the auto-build CLI path (CLI `--model` overrides). | `cli/main.py` |
| `UTILITY_MODEL_ID` | built-in default | No | Model ID for small utility LLM calls (e.g. commit messages). | `core/model_config.py` |
| `UTILITY_THINKING_BUDGET` | unset | No | Thinking-token budget for utility calls. | `core/model_config.py` |

---

## Knowledge graph (Graphiti / Graphiti-MCP)

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `GRAPHITI_ENABLED` | off | No | Default OFF | Enable the Graphiti knowledge-graph integration. | `integrations/graphiti/config.py` |
| `GRAPHITI_LLM_PROVIDER` | `openai` | No | — | LLM provider for Graphiti. | `integrations/graphiti/config.py` |
| `GRAPHITI_EMBEDDER_PROVIDER` | derived | No | — | Embedder provider for Graphiti. | `integrations/graphiti/config.py`, `query_memory.py` |
| `GRAPHITI_DATABASE` | built-in default | No | — | Graphiti database name/type. | `integrations/graphiti/config.py` |
| `GRAPHITI_DB_PATH` | built-in default | No | — | Graphiti database path. | `integrations/graphiti/config.py` |
| `GRAPHITI_MCP_URL` | `http://localhost:3102/mcp/` | No | — | Graphiti MCP server URL; presence enables the MCP-backed memory client. | `core/client.py`, `agents/tools_pkg/models.py` |
| `INSIGHT_EXTRACTION_ENABLED` | `true` | No | Default ON | Enable post-run insight extraction into memory. | `analysis/insight_extractor.py` |
| `INSIGHT_EXTRACTOR_MODEL` | built-in default | No | — | Model used for insight extraction. | `analysis/insight_extractor.py` |

---

## Miscellaneous and diagnostics

| Variable | Default | Required | On/Off | Purpose | Read in |
|---|---|---|---|---|---|
| `DEBUG` | off | No | Default OFF | Enable debug logging across the backend. | `core/debug.py`, `core/client.py`, `core/phase_event.py`, `ui/status.py` |
| `DEBUG_LEVEL` | `1` | No | — | Debug verbosity level. | `core/debug.py` |
| `DEBUG_LOG_FILE` | unset | No | — | Path to write the debug log file. | `core/debug.py` |
| `QUICK_MODE` | off | No | Default OFF | Use shorter/faster prompts (`true` to enable). | `prompts_pkg/prompts.py` |
| `USE_CLAUDE_MD` | off | No | Default OFF | Load repo `CLAUDE.md` into the agent context (`true` to enable). | `core/client.py` |
| `ENABLE_FANCY_UI` | `true` | No | Default ON | Enable the rich terminal UI. | `ui/capabilities.py` |
| `NO_COLOR` | unset | No | — | Standard: disable colored output when set. | `ui/capabilities.py`, `cli/mcp_commands.py` |
| `FORCE_COLOR` | unset | No | — | Standard: force colored output when set. | `ui/capabilities.py` |
| `TERM` | unset | No | — | Terminal type, used for UI capability detection. | `ui/capabilities.py` |
| `CI` | unset | No | — | When `true`, the reviewer runs in non-interactive CI mode. | `review/reviewer.py` |
| `EDITOR` | unset | No | — | Editor invoked by the interactive reviewer. | `review/reviewer.py` |
| `FACTORY_SERVICE_NAME` | `the Factory` | No | — | Service name used in GitLab-provider messages. | `runners/github/providers/gitlab_provider.py` |
| `JOB_ID` | injected | No | — | Job identifier; injected into the verify pipeline / gen-functional by the dispatcher. | `agents/verify_pipeline.py`, `agents/gen_functional.py` |
| `CORRELATION_KEY` | injected | No | — | Correlation key for a run; injected into the verify pipeline by the dispatcher. | `agents/verify_pipeline.py` |

---

## Portal variables (web-server)

These belong to the web-server portal app (`apps/web-server`), not the
backend, but appear in the root `.env.example` because the docker-compose
deployment sets them on the same container. See
`apps/web-server/.env.example` for the authoritative list.

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `HOST_PORT` | `3102` | No | Host port the docker-compose web server is published to. |
| `APP_API_TOKEN` | auto-generated | No | Portal API token (auto-generated on first run if unset). |
| `APP_CORS_ORIGINS` | unset | No | Extra CORS origins (comma-separated or JSON list). |
| `TFACTORY_DATA_DIR` | `./data` | No | Bind-mounted host dir for the container's `/home/nonroot/.tfactory`. |

---

## Completeness

This reference was produced by grepping the entire `apps/backend` tree for
every environment read (`os.environ`, `os.getenv`, `getenv(`, plus dict-style
`env.get(...)` and indirection through `_ENV_*` constants), then reconciling.

- **Distinct variable names found:** 155
- **Documented above:** 153
- **Intentionally excluded (incidental):** 2
- **Unaccounted:** 0

### Intentionally excluded (incidental)

| Variable | Why excluded |
|---|---|
| `PORT` | Not TFactory config. Appears only in `analysis/analyzers/port_detector.py` as a comment describing the pattern the analyzer scans for in the **target** application under test. |
| `PYTHONPATH` | Not read as configuration. TFactory only **sets** it when building subprocess/verify-Job environments (`agents/evaluator.py`, `agents/verify_dispatch.py`). |

The following variables appear in `.env.example` for the test/e2e harness or
the web-server app and are **not** read by the backend service code; they are
listed here for completeness and are safe to ignore for a production install:
`TFACTORY_E2E_STATE_DIR`, `TFACTORY_AIFACTORY_BRANCH`, `TFACTORY_AIFACTORY_PR`,
`TFACTORY_DOCKER_IMAGE_PYTHON` (test/e2e harness), and `APP_PORT` (web-server).

Two names that showed up in the raw grep but are **not** real variables:
`VAR` and `VAR_NAME` — both are placeholder strings inside docstrings/error
messages (`security/git_validators.py`, `analysis/analyzers/context/env_detector.py`).

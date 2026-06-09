# Ollama Cloud (OpenAI-compatible provider)

> Run TFactory's agent pipeline on **Ollama Cloud** — open models like
> `qwen3-coder:480b` and `gpt-oss:120b` — without standing up any new provider.
> Issue [#306](https://github.com/olafkfreund/TFactory/issues/306).

## Why this isn't a new provider

Ollama Cloud is an **OpenAI-compatible** endpoint at `https://ollama.com/v1`
(`/v1/models`, `/v1/chat/completions`). TFactory already has a generic
`openai-compatible` provider (`apps/backend/providers/openai_compatible.py`),
so Ollama Cloud is just a base URL + a key — no `ollama_cloud.py` needed.

> **Cloud, not local.** The *local* Ollama daemon (`http://localhost:11434`,
> the `ollama` provider) is unauthenticated and unreachable from the k3d
> cluster. Ollama **Cloud** is reachable (pods have public egress) and
> **requires a real API key** minted at <https://ollama.com/settings/keys>.

## Configure

Set two env vars on the backend (the provider falls back to them when no
per-user endpoint is saved in the portal — see
`phase_config.get_provider_extra_kwargs`):

```bash
OPENAI_COMPATIBLE_BASE_URL=https://ollama.com   # the /v1 suffix is appended for you
OPENAI_COMPATIBLE_API_KEY=<your ollama key>
```

Then create a task whose **model string** carries the `openai-compatible:`
prefix and the bare Ollama model name:

```
openai-compatible:qwen3-coder:480b
```

Resolution path:

1. `infer_provider_from_model("openai-compatible:…")` → the `openai-compatible` provider.
2. `strip_provider_prefix` yields the bare model `qwen3-coder:480b`.
3. With no saved DB endpoint, `get_provider_extra_kwargs` falls back to the env vars above.
4. The provider POSTs `https://ollama.com/v1/chat/completions` with `Authorization: Bearer <key>`.

## Verify connectivity

Before kicking off a task, confirm the key + egress work and list the models
your key can see:

```bash
cd apps/backend
python -m providers.ollama_cloud_check
# or explicitly:
python -m providers.ollama_cloud_check --base-url https://ollama.com --api-key sk-...
```

It GETs `https://ollama.com/v1/models` with the key and prints the available
cloud models; exit code is `0` only on success.

## Models (observed on the current key)

| Works | Needs a paid plan (HTTP 403) |
|---|---|
| `gpt-oss:120b`, `gpt-oss:20b`, `qwen3-coder:480b`, `gemma3:27b` | `glm-5`, `deepseek-v3.1:671b` |

Browse cloud models at <https://ollama.com/search?c=cloud>.

## Caveats

- **JSON adherence.** Open models follow strict-JSON instructions less reliably
  than Claude. JSON-parsing steps in the pipeline are tolerant (brace-matching /
  empty-response fallback), but expect the occasional non-fatal parse warning.
- **Test toolchain allowlist.** If you want the agent to *run* tests in a given
  language, make sure that toolchain is in the command allowlist
  (`core/security.py` / `context/project_analyzer.py`).

## Deployment (factory-gitops — separate repo)

Production wiring lives outside this repo: add `OPENAI_COMPATIBLE_API_KEY` to
the `factory-secrets` k8s secret (sourced from the agenix key on the host — it
is **not** automatically in the cluster) and reference it in this service's
deployment env under `factory-gitops`. This guide + the connectivity check
cover everything inside the TFactory repo itself.

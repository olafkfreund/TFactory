# LLM providers

TFactory uses the **Claude Agent SDK** (`claude-agent-sdk`) as its primary provider,
but supports many others through the provider factory in `apps/backend/providers/`.
Provider selection is driven by the **model string** —
`phase_config.infer_provider_from_model()`.

!!! warning "Golden rule"
    Never call `anthropic.Anthropic()` directly. Route Claude interactions through
    `core.client.create_client()`, and every other provider through
    `providers.factory.get_provider()`.

## Supported providers

| Provider | Model pattern → module | Notes |
|----------|------------------------|-------|
| **Claude (Agent SDK)** | default → `core/client.py` | Pre-configured security, MCP, tool permissions, sessions. |
| **Codex CLI** | `gpt-*` / `*codex*` → `codex_agentic.py` | Writes an api-key `auth.json` into a TFactory-owned `CODEX_HOME` (`~/.tfactory/codex-home/`) when `OPENAI_API_KEY` is set; leaves global `codex login` untouched. |
| **GitHub Copilot CLI** | `copilot:<model>` → `copilot_agentic.py` | Runs `copilot -p … --allow-all-tools --model <model>` headlessly. Models: `claude-sonnet-4.5` (default), `claude-sonnet-4`, `gpt-5`. |
| **Gemini** | `gemini-*` → `gemini_agentic.py` | `google-generativeai`. |
| **Ollama** | local models → `ollama_agentic.py` | Fully local. |
| **OpenAI-compatible** | endpoint-driven → `openai_compatible_agentic.py` | LM Studio, vLLM, OpenRouter, Together, Groq, LocalAI. |

The factory lives in `providers/factory.py`; provider interfaces are in
`providers/types.py`.

## BYO-LLM / air-gapped (#38)

`byo_llm.py` classifies a model+endpoint's data-egress posture so the portal/CLI can
show an honest badge:

| Posture | Meaning |
|---------|---------|
| `LOCAL` | runs on this machine — no egress |
| `SELF_HOSTED` | your network — controlled egress |
| `MANAGED_CLOUD` | third-party API — data leaves your network |

```bash
python apps/backend/byo_llm.py <model>   # exit 0 only if all data stays on your network
```

See `guides/byo-llm.md`.

## Memory provider matrix

Graphiti memory (`integrations/graphiti/`) is multi-provider too:

- **LLM:** OpenAI, Anthropic, Azure OpenAI, Ollama, Google AI (Gemini).
- **Embedders:** OpenAI, Voyage AI, Azure OpenAI, Ollama, Google AI.

Configured via `apps/backend/.env` (`graphiti_config.py` + `graphiti_providers.py`).

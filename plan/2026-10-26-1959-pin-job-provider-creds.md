---
status: approved
issue: olafkfreund/Factory#1959
spec: spec/2026-10-26-1959-pin-job-provider-creds.md
---

# Plan: Pin the provider credentials a dispatched TFactory Job receives

Test-only slice. Decisions carried from the spec: pin the model-provider
credential names in the real Job env (suffix `_API_KEY` / `_AUTH_TOKEN` /
`_OAUTH_TOKEN`) to exactly ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN, OPENAI_API_KEY, OPENAI_COMPATIBLE_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEY, OLLAMA_API_KEY;
`ANTHROPIC_API_KEY` must stay out; do not change `apps/backend/agents/verify_dispatch.py`.

## Steps

1. `tests/test_verify_dispatch.py` (append): add the pin test → verify by running it with pytest
   (passes), then temporarily adding `MISTRAL_API_KEY` to `_SDK_PASSTHROUGH_ENV` (apps/backend/agents/verify_dispatch.py:197-266)
   and confirming it fails; revert that edit.
   Traps: no source change; clear the env with monkeypatch so the host env does not leak in.

## Tests

`pytest tests/test_verify_dispatch.py -q` passes; ruff clean on the test file.

## Rollback

Revert the commit. No runtime effect.

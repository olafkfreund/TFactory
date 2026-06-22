"""Local-LLM / Ollama / OpenAI-compat settings endpoints — from settings.py (#360).

A focused sub-router for local-LLM provider detection + Ollama / OpenAI-compatible
model listing, pulling and connectivity tests, carved out of routes/settings.py.
Behaviour and paths unchanged; main.py mounts it under the same /api/settings
prefix. Shared helpers/models still live in routes/settings.py and are imported.

    GET  /api/settings/local-llm/detect | ollama/models | openai-compat/models
    POST /api/settings/openai-compat/test | ollama/pull | ollama/test
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/local-llm/detect")
async def detect_local_llm_providers():
    """Detect locally installed/running LLM providers via CLI and process checks.

    Uses ``shutil.which`` for binary detection, ``subprocess`` for version
    and model list commands, and ``pgrep`` for running-process checks.
    No HTTP port probing — instant and avoids false positives.
    """
    import asyncio
    import shutil

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _run(cmd: list[str], timeout: float = 3.0) -> tuple[bool, str]:
        """Run *cmd* asynchronously, return (ok, stdout)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode == 0, (stdout or b"").decode().strip()
        except Exception:
            return False, ""

    async def _is_process_running(name: str) -> bool:
        ok, _ = await _run(["pgrep", "-x", name], timeout=2.0)
        return ok

    # ------------------------------------------------------------------
    # Per-provider detection
    # ------------------------------------------------------------------

    async def detect_ollama() -> dict:
        result = {
            "id": "ollama",
            "name": "Ollama",
            "url": "http://localhost:11434",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        if not shutil.which("ollama"):
            return result
        result["installed"] = True

        ok, out = await _run(["ollama", "--version"])
        if ok and out:
            # "ollama version is 0.14.3" → "0.14.3"
            result["version"] = out.split()[-1] if out else ""

        result["running"] = await _is_process_running("ollama")

        # `ollama list` works when the server is running
        ok, out = await _run(["ollama", "list"], timeout=5.0)
        if ok and out:
            lines = out.strip().splitlines()
            # First line is a header row
            model_lines = [l for l in lines[1:] if l.strip()]
            all_names = [l.split()[0] for l in model_lines if l.split()]
            # Filter out embedding/reranker models — only show chat LLMs
            _embed_kw = {"embed", "minilm", "bge", "gte", "e5", "rerank"}
            model_names = [
                n for n in all_names if not any(kw in n.lower() for kw in _embed_kw)
            ]
            result["models"] = model_names
            result["modelCount"] = len(model_names)
            if model_names:
                result["detected"] = True
                result["running"] = True  # list worked ⇒ server is up
        elif result["running"]:
            # Server is running but no models pulled yet
            result["detected"] = True

        return result

    async def detect_lmstudio() -> dict:
        result = {
            "id": "lmstudio",
            "name": "LM Studio",
            "url": "http://localhost:1234",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # LM Studio CLI
        if shutil.which("lms"):
            result["installed"] = True
            ok, out = await _run(["lms", "version"])
            if ok and out:
                result["version"] = out.strip()
            ok, out = await _run(["lms", "status"])
            if ok and "running" in out.lower():
                result["running"] = True
                result["detected"] = True
            ok, out = await _run(["lms", "ls"])
            if ok and out:
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                result["models"] = lines
                result["modelCount"] = len(lines)
        # Fallback: check for running process
        if not result["installed"]:
            if await _is_process_running("lm-studio") or await _is_process_running(
                "lmstudio"
            ):
                result["detected"] = True
                result["running"] = True
        return result

    async def detect_localai() -> dict:
        result = {
            "id": "localai-llamacpp",
            "name": "LocalAI / llama.cpp",
            "url": "http://localhost:8080",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # LocalAI binary
        if shutil.which("local-ai"):
            result["installed"] = True
            ok, out = await _run(["local-ai", "--version"])
            if ok and out:
                result["version"] = out.strip()
            result["detected"] = True
        # llama-server (llama.cpp)
        elif shutil.which("llama-server") or shutil.which("llama-cpp-server"):
            result["installed"] = True
            result["detected"] = True
        # Process check
        for proc_name in ("local-ai", "llama-server", "llama-cpp-server"):
            if await _is_process_running(proc_name):
                result["running"] = True
                result["detected"] = True
                break
        return result

    async def detect_vllm() -> dict:
        result = {
            "id": "vllm",
            "name": "vLLM",
            "url": "http://localhost:8000",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # vLLM is a Python package
        ok, out = await _run(["python3", "-c", "import vllm; print(vllm.__version__)"])
        if ok and out:
            result["installed"] = True
            result["version"] = out.strip()
            result["detected"] = True
        # Process check
        if await _is_process_running("vllm"):
            result["running"] = True
            result["detected"] = True
        return result

    async def detect_jan() -> dict:
        result = {
            "id": "jan",
            "name": "Jan",
            "url": "http://localhost:1337",
            "detected": False,
            "installed": False,
            "running": False,
            "version": "",
            "modelCount": 0,
            "models": [],
        }
        # Jan is an Electron app
        if shutil.which("jan"):
            result["installed"] = True
            result["detected"] = True
        # Check common install locations
        jan_paths = [
            Path("/opt/jan/jan"),
            Path.home() / ".local" / "bin" / "jan",
            Path("/usr/bin/jan"),
        ]
        for p in jan_paths:
            if p.exists():
                result["installed"] = True
                result["detected"] = True
                break
        # Process check
        if await _is_process_running("jan"):
            result["running"] = True
            result["detected"] = True
        return result

    # ------------------------------------------------------------------
    # Run all detections concurrently
    # ------------------------------------------------------------------
    results = await asyncio.gather(
        detect_ollama(),
        detect_lmstudio(),
        detect_localai(),
        detect_vllm(),
        detect_jan(),
    )

    return {"providers": list(results)}


@router.get("/ollama/models")
async def list_ollama_models(
    ollamaBaseUrl: str = Query(default="http://localhost:11434"),
):
    """List available Ollama models."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{ollamaBaseUrl}/api/tags")
            response.raise_for_status()
            data = response.json()

            # Extract model list, filtering out embedding-only models
            embedding_keywords = {"embed", "minilm", "bge", "gte", "e5"}
            embedding_families = {"bert", "nomic-bert"}
            models = []
            for model in data.get("models", []):
                name_lower = model["name"].lower()
                details = model.get("details", {})
                families = {f.lower() for f in details.get("families", [])}

                # Skip embedding models (family is bert-based or name contains embedding keywords)
                if families & embedding_families:
                    continue
                if any(kw in name_lower for kw in embedding_keywords):
                    continue

                models.append(
                    {
                        "name": model["name"],
                        "size": model["size"],
                        "modified": model["modified_at"],
                        "details": details,
                    }
                )

            return {"models": models}
    except Exception as e:
        logger.warning(f"Failed to list Ollama models: {e}")
        return {"success": False, "error": str(e)}


@router.get("/openai-compat/models")
async def list_openai_compat_models(
    baseUrl: str = Query(default="http://localhost:8080"),
    apiKey: str | None = Query(default=None),
):
    """List available models from an OpenAI-compatible server.

    Calls ``GET {baseUrl}/v1/models``, filters out embedding/reranker models,
    and returns ``{models: [{name: str}]}`` — the same envelope shape used by
    the Ollama models endpoint so callers can treat both identically.
    """
    try:
        import httpx

        headers: dict[str, str] = {}
        if apiKey:
            headers["Authorization"] = f"Bearer {apiKey}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{baseUrl}/v1/models", headers=headers)
            response.raise_for_status()
            data = response.json()

        # OpenAI-compatible /v1/models returns {"data": [{"id": "...", ...}, ...]}
        raw_models = data.get("data", [])

        # Filter out embedding / reranker models by common name keywords
        _embed_kw = {"embed", "embedding", "minilm", "bge", "gte", "e5", "rerank"}
        models = []
        for model in raw_models:
            model_id: str = model.get("id", "")
            name_lower = model_id.lower()
            if any(kw in name_lower for kw in _embed_kw):
                continue
            models.append({"name": model_id})

        return {"models": models}
    except Exception as e:
        logger.warning(f"Failed to list OpenAI-compatible models from {baseUrl}: {e}")
        return {"success": False, "error": str(e)}


class OpenAICompatTestRequest(BaseModel):
    """Request model for testing an OpenAI-compatible server connection."""

    baseUrl: str = Field(..., description="Base URL of the OpenAI-compatible server")
    apiKey: str | None = Field(None, description="Optional API key for authentication")


@router.post("/openai-compat/test")
async def test_openai_compat_connection(request: OpenAICompatTestRequest):
    """Test connectivity to an OpenAI-compatible server.

    Sends ``GET {baseUrl}/v1/models`` with a 5-second timeout. Returns the
    number of (non-embedding) models available so the caller can confirm the
    server is reachable and serving models.
    """
    try:
        import httpx

        headers: dict[str, str] = {}
        if request.apiKey:
            headers["Authorization"] = f"Bearer {request.apiKey}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{request.baseUrl}/v1/models", headers=headers)
            response.raise_for_status()
            data = response.json()

        raw_models = data.get("data", [])

        # Filter out embedding / reranker models (same keywords as the list endpoint)
        _embed_kw = {"embed", "embedding", "minilm", "bge", "gte", "e5", "rerank"}
        model_count = sum(
            1
            for m in raw_models
            if not any(kw in m.get("id", "").lower() for kw in _embed_kw)
        )

        return {
            "success": True,
            "modelCount": model_count,
            "message": f"Connected successfully. {model_count} model(s) available.",
        }
    except Exception as e:
        logger.warning(
            f"OpenAI-compatible connection test failed for {request.baseUrl}: {e}"
        )
        return {"success": False, "error": str(e)}


@router.post("/ollama/pull")
async def pull_ollama_model(
    modelName: str = Body(..., embed=True),
    ollamaBaseUrl: str = Body(default="http://localhost:11434", embed=True),
):
    """Pull (download) an Ollama model."""
    try:
        import httpx
        import json

        # Stream the pull progress
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", f"{ollamaBaseUrl}/api/pull", json={"name": modelName}
            ) as response:
                response.raise_for_status()

                # Stream progress updates
                async for line in response.aiter_lines():
                    if line:
                        progress_data = json.loads(line)
                        # Could emit WebSocket progress here
                        logger.info(f"Pull progress: {progress_data}")

                return {
                    "success": True,
                    "message": f"Model {modelName} pulled successfully",
                }
    except Exception as e:
        logger.warning(f"Failed to pull Ollama model: {e}")
        return {"success": False, "error": str(e)}


@router.post("/ollama/test")
async def test_ollama_connection(
    ollamaBaseUrl: str = Body(..., embed=True), modelName: str = Body(..., embed=True)
):
    """Test Ollama connection and model availability."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if server is reachable
            response = await client.get(f"{ollamaBaseUrl}/api/tags")
            response.raise_for_status()

            # Check if model exists
            data = response.json()
            models = [m["name"] for m in data.get("models", [])]

            if modelName not in models:
                return {
                    "success": False,
                    "error": f"Model '{modelName}' not found. Available models: {', '.join(models)}",
                }

            # Test model with simple query
            test_response = await client.post(
                f"{ollamaBaseUrl}/v1/chat/completions",
                json={
                    "model": modelName,
                    "messages": [{"role": "user", "content": "Test"}],
                    "max_tokens": 10,
                },
                timeout=30.0,
            )
            test_response.raise_for_status()

            return {"success": True, "message": "Connection successful!"}
    except Exception as e:
        logger.warning(f"Ollama connection test failed: {e}")
        return {"success": False, "error": str(e)}

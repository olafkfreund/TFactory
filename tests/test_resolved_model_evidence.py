"""A verify run must record which model ACTUALLY ran — #869.

The Factory hub's `docs/dev/validation-scorecard.md` (Factory#295) will only
accept a benchmark cell backed by the model that executed, "not what was
requested" — the doc lives in the hub, not here. TFactory had
a `usage.model` field for exactly that and it came back EMPTY on a confirmed
Claude run, because the Claude Agent SDK's `ResultMessage` has no `.model`
attribute at all — and the existing unit tests used a fake that did, so the
suite stayed green over a field that was always blank in production.

Two facts are recorded, and they are not the same fact:

  requested_model  what the seam asked for
  model            what actually served the tokens, per the provider

Both directions are proved here: a run on a known model records that model, and
a run that resolves to something ELSE records the substitute, not the request.
A field that is always empty and a field that is always the request look
identical in a suite that only ever checks the happy path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents.session import requested_model, run_agent_session
from providers.types import AssistantMessage as ShimAssistantMessage
from providers.types import TextBlock
from task_logger import LogPhase
from usage import model_from_model_usage, record_in_status, usage_from_obj

# ── stand-ins for the SDK messages ─────────────────────────────────────────
#
# Hand-rolled rather than imported, because the real ResultMessage takes six
# required fields that say nothing about what is under test here. They are
# pinned to the real shapes two ways: test_stand_ins_match_the_real_sdk_types
# below reads the installed package in a clean subprocess, and
# tests/test_sdk_shape_contract.py asserts the same facts against the module
# the suite itself imports.
#
# Historically these could not import claude_agent_sdk at all: conftest replaced
# it with a MagicMock for every test, and a MagicMock answers
# `hasattr(msg, "model")` truthfully-for-anything — the structural reason
# nothing could notice the real ResultMessage has no `.model` (#869). The mock
# is now conditional on the package being genuinely absent (#882).
#
# The session loop dispatches on ``type(msg).__name__``, never isinstance, so
# these class NAMES are load-bearing.


class ResultMessage:
    """The SDK's ResultMessage as it ACTUALLY is: no ``.model`` attribute.

    Deliberately not given one. The per-model breakdown lives in
    ``model_usage``, keyed by model id with camelCase token counts (passed
    through verbatim from the CLI).
    """

    def __init__(
        self,
        model_usage: dict[str, dict[str, int]],
        *,
        total_cost_usd: float | None = None,
    ) -> None:
        self.usage = {
            "input_tokens": sum(v.get("inputTokens", 0) for v in model_usage.values()),
            "output_tokens": sum(
                v.get("outputTokens", 0) for v in model_usage.values()
            ),
        }
        self.model_usage = model_usage
        self.session_id = "sess-869"
        if total_cost_usd is not None:
            self.total_cost_usd = total_cost_usd


class AssistantMessage:
    """The SDK's AssistantMessage: content blocks PLUS the model that served."""

    def __init__(self, text: str, model: str) -> None:
        self.content = [TextBlock(text=text)]
        self.model = model


def test_stand_ins_match_the_real_sdk_types() -> None:
    """Pin the three SDK facts the reader in ``usage.py`` depends on.

    Read from the INSTALLED package in a clean interpreter, so the answer holds
    no matter what the suite has in ``sys.modules``. If a future SDK release
    moves any of these, this goes red and names what to update — instead of
    ``usage.model`` quietly going blank again.
    """
    probe = (
        "import json;"
        "from claude_agent_sdk.types import AssistantMessage as A, ResultMessage as R;"
        "print(json.dumps({'result': sorted(R.__annotations__),"
        " 'assistant': sorted(A.__annotations__)}))"
    )
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        pytest.skip("claude_agent_sdk is not importable in this interpreter")
    shapes = json.loads(done.stdout)

    assert "model" not in shapes["result"], (
        "ResultMessage grew a .model field — usage.py can now read it directly "
        "instead of deriving one from model_usage"
    )
    assert "model_usage" in shapes["result"], (
        "ResultMessage.model_usage is gone — usage.model_from_model_usage reads "
        "it as the token-weighted source of the model that actually ran (#869)"
    )
    assert "model" in shapes["assistant"], (
        "AssistantMessage.model is gone — agents.session reads it per turn as "
        "the primary resolved-model evidence (#869)"
    )


class _Client:
    """Minimal stand-in for a client run_agent_session can drive."""

    def __init__(self, model: str, messages: tuple[Any, ...]) -> None:
        self._model = model  # where every provider adapter parks its model
        self._messages = messages

    async def query(self, prompt: str) -> None:  # noqa: ARG002 - shape only
        return None

    async def receive_response(self):
        for msg in self._messages:
            yield msg


# ── unit: the id comes from what served, not from what was asked ───────────


def test_result_message_has_no_model_attribute_and_still_names_one() -> None:
    """The exact production bug: a real Claude run left ``usage.model`` empty."""
    msg = ResultMessage(
        {"claude-opus-4-8-20260115": {"inputTokens": 206, "outputTokens": 82613}},
        total_cost_usd=7.76388,
    )
    assert not hasattr(msg, "model"), "the real SDK type has no .model — see #869"

    recorded = usage_from_obj(msg)
    assert recorded is not None
    assert recorded.model == "claude-opus-4-8-20260115"
    assert recorded.input_tokens == 206
    assert recorded.cost_usd == 7.76388


def test_model_usage_names_the_id_that_served_the_most_tokens() -> None:
    """A session that fell back mid-flight lists BOTH ids; pick the worker."""
    assert (
        model_from_model_usage(
            {
                "claude-opus-4-8-20260115": {"inputTokens": 10, "outputTokens": 5},
                "claude-haiku-4-5-20251001": {"inputTokens": 900, "outputTokens": 700},
            }
        )
        == "claude-haiku-4-5-20251001"
    )


def test_model_usage_never_guesses() -> None:
    for empty in (None, {}, "not-a-dict", 7):
        assert model_from_model_usage(empty) == ""


def test_requested_model_reads_either_client_shape() -> None:
    class _Options:
        model = "claude-opus-4-8"

    class _ClaudeClient:
        options = _Options()

    assert requested_model(_ClaudeClient()) == "claude-opus-4-8"
    assert requested_model(_Client("ollama:qwen3:14b", ())) == "ollama:qwen3:14b"
    assert requested_model(object()) == ""


# ── direction 1: a verify run against a known model records THAT model ─────


async def test_verify_records_the_model_that_ran(tmp_path: Path) -> None:
    """The Evaluator's phase is ``coding``; the record must name the model."""
    client = _Client(
        "claude-opus-4-8",
        (
            AssistantMessage("verdict: accept", "claude-opus-4-8-20260115"),
            ResultMessage(
                {"claude-opus-4-8-20260115": {"inputTokens": 206, "outputTokens": 800}},
                total_cost_usd=1.5,
            ),
        ),
    )

    await run_agent_session(client, "evaluate", tmp_path, False, LogPhase.CODING)

    usage = json.loads((tmp_path / "status.json").read_text())["usage"]
    assert usage["model"] == "claude-opus-4-8-20260115"
    (worker,) = usage["workers"]
    assert worker == {
        "worker_id": "coding",
        "phase": "coding",
        "provider": "claude",
        "requested_model": "claude-opus-4-8",
        "model": "claude-opus-4-8-20260115",
        "input_tokens": 206,
        "output_tokens": 800,
        "total_tokens": 1006,
        "cost_usd": 1.5,
    }


# ── direction 2: a run that resolves elsewhere records the substitute ──────


async def test_verify_records_the_fallback_not_the_request(tmp_path: Path) -> None:
    """THE case the field exists for: asked for opus, haiku actually ran.

    If this recorded the request, a benchmark table would credit opus with a
    result opus never produced — confidently wrong data, worse than a blank.
    """
    client = _Client(
        "claude-opus-4-8",  # what the seam resolved and asked for
        (
            # The API answered on a different model; the SDK says so per turn.
            AssistantMessage("verdict: accept", "claude-haiku-4-5-20251001"),
            ResultMessage(
                {"claude-haiku-4-5-20251001": {"inputTokens": 50, "outputTokens": 90}}
            ),
        ),
    )

    await run_agent_session(client, "evaluate", tmp_path, False, LogPhase.CODING)

    (worker,) = json.loads((tmp_path / "status.json").read_text())["usage"]["workers"]
    assert worker["requested_model"] == "claude-opus-4-8"
    assert worker["model"] == "claude-haiku-4-5-20251001", (
        "recorded the requested model instead of the one that ran — a fallback "
        "this silent is exactly how #869 mis-credits a benchmark cell"
    )
    # ... and the divergence is legible from the artifact alone.
    assert worker["model"] != worker["requested_model"]


async def test_mid_session_fallback_records_the_last_model_seen(
    tmp_path: Path,
) -> None:
    """Downgraded part-way through: the model that finished the work wins."""
    client = _Client(
        "claude-opus-4-8",
        (
            AssistantMessage("thinking", "claude-opus-4-8-20260115"),
            AssistantMessage("verdict: accept", "claude-haiku-4-5-20251001"),
            ResultMessage(
                {"claude-haiku-4-5-20251001": {"inputTokens": 5, "outputTokens": 9}}
            ),
        ),
    )

    await run_agent_session(client, "evaluate", tmp_path, False, LogPhase.CODING)

    (worker,) = json.loads((tmp_path / "status.json").read_text())["usage"]["workers"]
    assert worker["model"] == "claude-haiku-4-5-20251001"


# ── the non-Claude cells: no tokens reported, but a model still ran ────────


async def test_ollama_verify_records_its_model_despite_zero_tokens(
    tmp_path: Path,
) -> None:
    """Ollama reports no token counts and costs nothing — the id is all we get.

    Before #869 this run wrote NOTHING, so a cell claiming "verified on Ollama"
    had no evidence at all behind it.
    """
    client = _Client(
        "ollama:qwen3:14b",
        # The provider shim stamps the id the server echoed; note the tag the
        # server actually loaded differs from the string that was requested.
        (ShimAssistantMessage(content=[TextBlock(text="ok")], model="qwen3:14b-q4"),),
    )

    await run_agent_session(client, "evaluate", tmp_path, False, LogPhase.CODING)

    usage = json.loads((tmp_path / "status.json").read_text())["usage"]
    (worker,) = usage["workers"]
    assert worker["provider"] == "ollama"
    assert worker["requested_model"] == "ollama:qwen3:14b"
    assert worker["model"] == "qwen3:14b-q4"
    assert worker["total_tokens"] == 0
    assert worker["cost_usd"] == 0.0


async def test_a_provider_that_reports_nothing_stays_blank(tmp_path: Path) -> None:
    """The CLI shims cannot observe a model. Blank must stay blank, not default.

    An empty ``model`` reads as UNKNOWN on the scorecard. Filling it from the
    request would turn "we don't know" into a confident claim.
    """
    client = _Client(
        "gpt-5-codex",
        (ShimAssistantMessage(content=[TextBlock(text="ok")]),),  # no model reported
    )

    await run_agent_session(client, "evaluate", tmp_path, False, LogPhase.CODING)

    (worker,) = json.loads((tmp_path / "status.json").read_text())["usage"]["workers"]
    assert worker["requested_model"] == "gpt-5-codex"
    assert worker["model"] == "", "a request must never be promoted to evidence"


# ── the provider adapters must pass the SERVED id up ───────────────────────


async def test_ollama_adapter_reports_the_tag_the_server_loaded(
    tmp_path: Path,
) -> None:
    """Ollama echoes the model it resolved, which is not always the one asked."""
    from providers.ollama_agentic import OllamaAgenticProvider

    provider = OllamaAgenticProvider(
        model="qwen3:14b", working_dir=tmp_path, tool_names=["Read"]
    )
    served = {
        "model": "qwen3:14b-instruct-q4_K_M",  # the tag actually loaded
        "message": {"role": "assistant", "content": "done"},
    }
    with patch.object(provider, "_http_post", return_value=served):
        await provider.query("go")
        messages = [m async for m in provider.receive_response()]

    assert messages[-1].model == "qwen3:14b-instruct-q4_K_M"


async def test_openai_compatible_adapter_reports_the_served_model(
    tmp_path: Path,
) -> None:
    """A gateway that substitutes a model says so in the response body."""
    from providers.openai_compatible_agentic import OpenAICompatibleAgenticProvider

    provider = OpenAICompatibleAgenticProvider(
        model="gpt-5", working_dir=tmp_path, tool_names=["Read"]
    )
    served = {
        "model": "gpt-5-mini-2026-04-01",  # the gateway routed elsewhere
        "choices": [{"message": {"role": "assistant", "content": "done"}}],
    }
    with patch.object(provider, "_http_post", return_value=served):
        await provider.query("go")
        messages = [m async for m in provider.receive_response()]

    assert messages[-1].model == "gpt-5-mini-2026-04-01"


def test_adapter_message_defaults_to_no_model() -> None:
    """The CLI shims construct without one; the field must stay additive."""
    assert ShimAssistantMessage(content=[TextBlock(text="x")]).model == ""


# ── accumulation + back-compat ─────────────────────────────────────────────


def test_phases_accumulate_side_by_side(tmp_path: Path) -> None:
    """Planning and verification can run on different models; keep both."""
    from usage import RunUsage, usage_block_from_status

    record_in_status(
        tmp_path,
        RunUsage(input_tokens=10, output_tokens=1, cost_usd=0.1),
        phase="planning",
        requested_model="opus",
        resolved_model="claude-opus-4-8-20260115",
    )
    for _ in range(2):  # two evaluator sessions in one verify
        record_in_status(
            tmp_path,
            RunUsage(input_tokens=100, output_tokens=20, cost_usd=0.5),
            phase="coding",
            requested_model="sonnet",
            resolved_model="claude-sonnet-4-6-20260210",
        )

    workers = usage_block_from_status(tmp_path)["workers"]
    assert [w["worker_id"] for w in workers] == ["coding", "planning"]
    coding = workers[0]
    assert coding["model"] == "claude-sonnet-4-6-20260210"
    assert (coding["input_tokens"], coding["output_tokens"]) == (200, 40)
    assert coding["cost_usd"] == 1.0


def test_a_later_blind_session_cannot_erase_observed_evidence(tmp_path: Path) -> None:
    from usage import RunUsage, usage_block_from_status

    record_in_status(
        tmp_path,
        RunUsage(input_tokens=10, output_tokens=1),
        phase="coding",
        requested_model="opus",
        resolved_model="claude-opus-4-8-20260115",
    )
    record_in_status(
        tmp_path,
        RunUsage(input_tokens=5, output_tokens=1),
        phase="coding",
        requested_model="opus",
        resolved_model="",  # a retry that could not observe one
    )
    (worker,) = usage_block_from_status(tmp_path)["workers"]
    assert worker["model"] == "claude-opus-4-8-20260115"
    assert worker["total_tokens"] == 17


def test_workers_key_is_omitted_when_no_phase_was_given(tmp_path: Path) -> None:
    """Additive: a caller that passes no phase gets the pre-#869 block."""
    from usage import RunUsage, usage_block_from_status

    record_in_status(tmp_path, RunUsage(input_tokens=10, output_tokens=1, model="m"))
    assert "workers" not in usage_block_from_status(tmp_path)


def test_completion_event_carries_the_worker_records(tmp_path: Path) -> None:
    """Which is how CFactory and the scorecard actually see it."""
    from agents.triager import _build_completion_envelope
    from usage import RunUsage

    (tmp_path / "status.json").write_text(json.dumps({"task_id": "869"}))
    record_in_status(
        tmp_path,
        RunUsage(input_tokens=10, output_tokens=1, cost_usd=0.2),
        phase="coding",
        requested_model="opus",
        resolved_model="claude-opus-4-8-20260115",
    )
    env = _build_completion_envelope(
        tmp_path, {"task_id": "869", "status": "triaged", "verdicts_count": 3}
    )
    (worker,) = env["usage"]["workers"]
    assert worker["phase"] == "coding"
    assert worker["model"] == "claude-opus-4-8-20260115"

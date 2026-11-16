"""Anti-loop guardrail and run limits (TFactory#1510, port of AIFactory#2033)."""

import asyncio
from types import SimpleNamespace

import pytest

from agents import run_guard as g

P = "TFACTORY_"


def _fail(tool="Edit", args=None):
    return {"cwd": "/w", "tool_name": tool, "tool_input": args or {"f": "a"}}


def test_guardrail_on_by_default_and_off_switch(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    assert g.guardrail_enabled()
    for off in ("off", "0", "false", "NO"):
        monkeypatch.setenv(P + "ACT_GUARDRAIL", off)
        assert not g.guardrail_enabled()


def test_max_turns_env(monkeypatch):
    monkeypatch.delenv(P + "MAX_TURNS", raising=False)
    assert g.max_turns() == 1000
    monkeypatch.setenv(P + "MAX_TURNS", "25")
    assert g.max_turns() == 25
    monkeypatch.setenv(P + "MAX_TURNS", "abc")
    assert g.max_turns() == 1000


def test_repeated_failure_blocks_then_tool_failure_halts(tmp_path, monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    g.register_session("/w", tmp_path)
    err = {**_fail(), "tool_response": {"is_error": True}}
    for _ in range(5):
        assert asyncio.run(g.guardrail_pretool_hook(_fail())) == {}
        asyncio.run(g.guardrail_posttool_hook(err))
    assert asyncio.run(g.guardrail_pretool_hook(_fail()))["decision"] == "block"
    assert g.guard_halt(tmp_path) is None  # one call blocked, session not halted
    for i in range(3):  # different args: same tool keeps failing -> halt
        asyncio.run(
            g.guardrail_posttool_hook(
                {**_fail(args={"f": str(i)}), "tool_response": {"is_error": True}}
            )
        )
    assert g.guard_halt(tmp_path).startswith("same_tool_failure")
    assert (
        asyncio.run(g.guardrail_pretool_hook(_fail(args={"f": "new"})))["decision"]
        == "block"
    )
    g.clear_halt(tmp_path)
    assert g.guard_halt(tmp_path) is None


def test_guardrail_off_never_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv(P + "ACT_GUARDRAIL", "off")
    g.register_session("/w", tmp_path)
    for _ in range(20):
        asyncio.run(
            g.guardrail_posttool_hook({**_fail(), "tool_response": {"is_error": True}})
        )
    assert asyncio.run(g.guardrail_pretool_hook(_fail())) == {}
    assert g.guard_halt(tmp_path) is None


def test_idempotent_read_no_progress_blocks(tmp_path, monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    g.register_session("/w", tmp_path)
    call = _fail("Read", {"file_path": "x"})
    for _ in range(5):
        asyncio.run(g.guardrail_posttool_hook({**call, "tool_response": "same"}))
    assert asyncio.run(g.guardrail_pretool_hook(call))["decision"] == "block"


def test_hook_error_never_raises(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    assert asyncio.run(g.guardrail_pretool_hook({"cwd": object()})) == {}
    assert asyncio.run(g.guardrail_posttool_hook({"cwd": object()})) == {}


def test_ceiling_env(monkeypatch):
    monkeypatch.delenv(P + "RUN_TOKEN_CEILING", raising=False)
    assert g.token_ceiling() == 10_000_000
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "0")
    assert g.token_ceiling() == 0
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "x")
    assert g.token_ceiling() == 10_000_000


def test_ceiling_exceeded_reads_persisted_usage(tmp_path, monkeypatch):
    from usage import RunUsage, record_in_status

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    assert g.ceiling_exceeded(tmp_path) is None
    record_in_status(tmp_path, RunUsage(input_tokens=60, output_tokens=40))
    assert g.ceiling_exceeded(tmp_path) is None  # exactly at the ceiling
    record_in_status(tmp_path, RunUsage(output_tokens=1))
    assert g.ceiling_exceeded(tmp_path).startswith("token_ceiling")
    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "0")
    assert g.ceiling_exceeded(tmp_path) is None


# ── run_agent_session wiring ──────────────────────────────────────────────


class ResultMessage:  # class NAME is load-bearing: the session dispatches on it
    def __init__(self, tokens: int) -> None:
        self.usage = {"input_tokens": tokens, "output_tokens": 0}
        self.session_id = "s"


class _Msg:
    content: tuple = ()


class _Client:
    def __init__(self, messages, on_message=None) -> None:
        self.queried = 0
        self.interrupted = 0
        self._messages = messages
        self._on = on_message

    async def query(self, prompt: str) -> None:  # noqa: ARG002
        self.queried += 1

    async def interrupt(self) -> None:
        self.interrupted += 1

    async def receive_response(self):
        for i, m in enumerate(self._messages):
            if self._on:
                self._on(i)
            yield m


async def test_session_over_ceiling_sends_no_query(tmp_path, monkeypatch):
    from agents.session import run_agent_session
    from task_logger import LogPhase
    from usage import RunUsage, record_in_status

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    record_in_status(tmp_path, RunUsage(input_tokens=500))
    client = _Client([ResultMessage(1)])
    status, reason, info = await run_agent_session(
        client, "x", tmp_path, False, LogPhase.CODING
    )
    assert (status, info["type"]) == ("error", "token_ceiling")
    assert reason.startswith("token_ceiling") and client.queried == 0


async def test_session_crossing_ceiling_records_usage_then_errors(
    tmp_path, monkeypatch
):
    from agents.session import run_agent_session
    from task_logger import LogPhase

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    client = _Client([ResultMessage(500)])
    status, _, info = await run_agent_session(
        client, "x", tmp_path, False, LogPhase.CODING
    )
    assert (status, info["type"]) == ("error", "token_ceiling")
    assert g.ceiling_exceeded(tmp_path)


async def test_guardrail_halt_interrupts_and_still_records_usage(tmp_path, monkeypatch):
    from agents.session import run_agent_session
    from task_logger import LogPhase
    from usage import usage_block_from_status

    monkeypatch.delenv(P + "RUN_TOKEN_CEILING", raising=False)
    g.register_session("/w", tmp_path)

    def halt_on_second(i: int) -> None:
        if i == 1:
            g._SESSIONS["/w"].ctl.halt_reason = "same_tool_failure: Edit failed 8x"

    client = _Client([_Msg(), _Msg(), ResultMessage(7)], halt_on_second)
    status, reason, info = await run_agent_session(
        client, "x", tmp_path, False, LogPhase.CODING
    )
    assert (status, info["type"]) == ("error", "guardrail_halt")
    assert reason.startswith("same_tool_failure") and client.interrupted == 1
    assert usage_block_from_status(tmp_path)["input_tokens"] == 7


def test_triager_envelope_and_pipeline_fail_over_ceiling(tmp_path, monkeypatch):
    from agents.triager import _build_completion_envelope
    from usage import RunUsage, record_in_status

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    record_in_status(tmp_path, RunUsage(input_tokens=500))
    status = {"status": "triaged", "verdicts_count": 3, "accepted_count": 3}
    env = _build_completion_envelope(tmp_path, status)
    assert env["outcome"] == "failure"
    assert env["halt_reason"].startswith("token_ceiling")

"""Anti-loop guardrail and run limits (TFactory#1510, port of AIFactory#2033)."""

import asyncio

from agents import run_guard as g

P = "TFACTORY_"


def _call(tool="Edit", args=None):
    return {"tool_name": tool, "tool_input": args or {"f": "a"}}


def _run(coro):
    return asyncio.run(coro)


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


def test_repeated_failure_blocks_then_tool_failure_halts(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    err = {**_call(), "tool_response": {"is_error": True}}
    for _ in range(5):
        assert _run(guard.pre(_call())) == {}
        _run(guard.post(err))
    assert _run(guard.pre(_call()))["decision"] == "block"
    assert g.guard_halt(_Holder(guard)) is None  # one call blocked, session not halted
    for _ in range(1):  # blocked repeats count: the 8th attempt halts the session
        assert _run(guard.pre(_call()))["decision"] == "block"
        assert g.guard_halt(_Holder(guard)) is None
    assert _run(guard.pre(_call()))["decision"] == "block"
    assert g.guard_halt(_Holder(guard)).startswith("same_tool_failure")


def test_distinct_failing_calls_never_halt(monkeypatch):
    """Failing test runs with different commands are normal iteration (#1525)."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    for i in range(20):
        c = _call("Bash", {"command": f"pytest t{i}.py"})
        assert _run(guard.pre(c)) == {}
        _run(guard.post({**c, "tool_response": {"is_error": True}}))
    assert g.guard_halt(_Holder(guard)) is None


class _Holder:
    """Stands in for a client carrying its guard, as create_client sets it up."""

    def __init__(self, guard):
        self._run_guard = guard


def test_failure_hook_counts_failed_calls(monkeypatch):
    """The SDK sends a failed tool call to PostToolUseFailure (payload has `error`)."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    for _ in range(5):
        _run(guard.failure({**_call(), "error": "boom"}))
    assert _run(guard.pre(_call()))["decision"] == "block"
    _run(guard.failure({**_call(), "error": "x", "is_interrupt": True}))  # not counted
    for i in range(8):  # different args: no halt
        _run(guard.failure({**_call(args={"f": str(i)}), "error": "x"}))
    assert g.guard_halt(_Holder(guard)) is None
    for _ in range(3):  # the blocked repeats of the identical call halt it
        _run(guard.pre(_call()))
    assert g.guard_halt(_Holder(guard)).startswith("same_tool_failure")


def test_guards_are_per_client(monkeypatch):
    """Two clients on one project (evaluator + review lane) never share state."""
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    a, b = g.Guard(), g.Guard()
    for _ in range(5):
        _run(a.failure({**_call(), "error": "boom"}))
    assert _run(a.pre(_call()))["decision"] == "block"
    assert _run(b.pre(_call())) == {}


def test_guardrail_off_never_blocks(monkeypatch):
    monkeypatch.setenv(P + "ACT_GUARDRAIL", "off")
    guard = g.Guard()
    for _ in range(20):
        _run(guard.post({**_call(), "tool_response": {"is_error": True}}))
        _run(guard.failure({**_call(), "error": "x"}))
    assert _run(guard.pre(_call())) == {}
    assert g.guard_halt(_Holder(guard)) is None


def test_idempotent_read_no_progress_blocks(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    call = _call("Read", {"file_path": "x"})
    for _ in range(5):
        _run(guard.post({**call, "tool_response": "same"}))
    assert _run(guard.pre(call))["decision"] == "block"


def test_hook_error_never_raises(monkeypatch):
    monkeypatch.delenv(P + "ACT_GUARDRAIL", raising=False)
    guard = g.Guard()
    bad = {"tool_name": ["unhashable"], "tool_input": object()}
    assert _run(guard.pre(bad)) == {}
    assert _run(guard.post(bad)) == {}
    assert _run(guard.failure(bad)) == {}
    assert g.guard_halt(object()) is None


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


def test_ceiling_ignores_usage_from_earlier_runs(tmp_path, monkeypatch):
    """A task that crossed the ceiling once can be verified again (#1525)."""
    from usage import RunUsage, record_in_status

    monkeypatch.setenv(P + "RUN_TOKEN_CEILING", "100")
    record_in_status(tmp_path, RunUsage(input_tokens=500))  # an earlier run
    assert g.ceiling_exceeded(tmp_path) is None  # first check of this run
    record_in_status(tmp_path, RunUsage(input_tokens=60))
    assert g.ceiling_exceeded(tmp_path) is None
    record_in_status(tmp_path, RunUsage(input_tokens=60))
    assert "run used 120 tokens" in g.ceiling_exceeded(tmp_path)


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
        self._run_guard = g.Guard()
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
    assert g.ceiling_exceeded(tmp_path) is None  # this run's baseline
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

    def halt_on_second(i: int) -> None:
        if i == 1:
            client._run_guard.ctl.halt_reason = "same_tool_failure: Edit failed 8x"

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
    assert g.ceiling_exceeded(tmp_path) is None  # this run's baseline
    record_in_status(tmp_path, RunUsage(input_tokens=500))
    status = {"status": "triaged", "verdicts_count": 3, "accepted_count": 3}
    env = _build_completion_envelope(tmp_path, status)
    assert env["outcome"] == "failure"
    assert env["halt_reason"].startswith("token_ceiling")


async def test_halted_judge_is_a_failed_vote(tmp_path, monkeypatch):
    from agents import evaluator

    async def _client(*_a):
        return object()

    async def _halted(*_a):
        return (
            "error",
            "m",
            {"type": "guardrail_halt", "message": "same_tool_failure: x"},
        )

    monkeypatch.setattr(evaluator, "_resolve_evaluator_client", _client)
    monkeypatch.setattr(evaluator, "_invoke_session", _halted)
    doc, phase, err = await evaluator._judge_once(tmp_path, tmp_path, "p", False)
    assert doc is None and phase == "evaluator_session_error"
    assert err.startswith("same_tool_failure")

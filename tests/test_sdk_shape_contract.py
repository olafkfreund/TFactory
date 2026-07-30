"""Every SDK message field TFactory reads must actually exist — #882.

TFactory reads fields off Claude Agent SDK message objects in half a dozen
places (``usage.py``, ``agents/session.py``, ``core/client.py``). Until #882
the whole suite ran against a ``MagicMock`` of the SDK, which answers
``hasattr(anything)`` with ``True`` — so a reader could name a field the real
type has never had and CI would still be green. That is exactly how #869 shipped:
``ResultMessage`` has no ``.model``, ``usage_from_obj`` read one anyway, and
every verify run recorded a blank model while the suite passed against a fake
that invented the attribute.

tests/conftest.py now stands in for the SDK only when it is genuinely absent,
so the imports below resolve to the REAL dataclasses wherever
``claude-agent-sdk`` is installed (CI, the service venv, the runtime image) and
to the explicit fake in ``tests/sdk_stub.py`` otherwise. Either way, a field
named here that does not exist fails the assertion instead of passing.

This is a contract, not coverage: each assertion names the reader that breaks
if the SDK moves, so a red line here says what to go fix.
"""

from __future__ import annotations

import dataclasses

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)


def _fields(cls: type) -> set[str]:
    """The attribute names an instance of ``cls`` actually carries."""
    return {f.name for f in dataclasses.fields(cls)}


# ── the field that was never there ─────────────────────────────────────────


def test_result_message_has_no_model_field() -> None:
    """The #869 bug, pinned. A ResultMessage cannot tell you what model ran.

    If this ever goes red because the SDK GREW a ``.model``, that is good news:
    ``usage.usage_from_obj`` can read it directly and
    ``usage.model_from_model_usage`` becomes a fallback rather than the source.
    """
    assert "model" not in _fields(ResultMessage), (
        "ResultMessage grew a .model field — usage.usage_from_obj can now read "
        "it directly instead of deriving one from model_usage (#869)"
    )


def test_a_result_message_instance_really_lacks_model() -> None:
    """Not just the annotation: the constructed object has no such attribute.

    The declaration and the instance are two different claims. A MagicMock
    passes the second one for any name at all, which is what #882 is about.
    """
    msg = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="sess-882",
        model_usage={"claude-opus-4-8-20260115": {"inputTokens": 5, "outputTokens": 3}},
    )
    assert not hasattr(msg, "model")
    with pytest.raises(AttributeError):
        _ = msg.model  # type: ignore[attr-defined]


# ── the fields the readers DO depend on ────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        ("usage", "usage.usage_from_obj — input_tokens / output_tokens"),
        ("total_cost_usd", "usage.usage_from_obj — the real cost, not an estimate"),
        ("model_usage", "usage.model_from_model_usage — which model actually ran"),
        ("session_id", "agents.session — correlates a run with the CLI transcript"),
        ("stop_reason", "agents.session — why the turn ended"),
        ("num_turns", "agents.session — turn accounting"),
        ("is_error", "agents.session — a failed session must not read as clean"),
    ],
)
def test_result_message_field_a_reader_depends_on(name: str, reader: str) -> None:
    assert name in _fields(ResultMessage), (
        f"ResultMessage.{name} is gone — breaks {reader}"
    )


@pytest.mark.parametrize(
    ("name", "reader"),
    [
        ("model", "agents.session — the resolved-model evidence, per turn (#869)"),
        ("content", "agents.session — the content blocks the loop walks"),
        ("stop_reason", "agents.session — turn termination"),
    ],
)
def test_assistant_message_field_a_reader_depends_on(name: str, reader: str) -> None:
    assert name in _fields(AssistantMessage), (
        f"AssistantMessage.{name} is gone — breaks {reader}"
    )


def test_assistant_message_carries_the_model_that_served() -> None:
    """The counterpart to ResultMessage: THIS is where the model id lives."""
    msg = AssistantMessage(content=[TextBlock(text="ok")], model="claude-opus-4-8")
    assert msg.model == "claude-opus-4-8"


# ── content-block shapes the session loop unpacks ──────────────────────────


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (TextBlock, {"text"}),
        (ThinkingBlock, {"thinking", "signature"}),
        (ToolUseBlock, {"id", "name", "input"}),
        (ToolResultBlock, {"tool_use_id", "content", "is_error"}),
    ],
)
def test_content_block_shape(block: type, expected: set[str]) -> None:
    """``agents.session`` unpacks blocks by attribute; a rename breaks it silently."""
    missing = expected - _fields(block)
    assert not missing, (
        f"{block.__name__} lost {sorted(missing)} — agents.session reads them"
    )


def test_message_envelope_shapes() -> None:
    """SystemMessage is what core.client synthesises for unknown CLI events."""
    assert {"subtype", "data"} <= _fields(SystemMessage)
    assert "content" in _fields(UserMessage)


# ── the guard itself ───────────────────────────────────────────────────────


def test_the_sdk_module_under_test_is_not_a_magicmock() -> None:
    """The regression that made every assertion above meaningless.

    A MagicMock satisfies each ``in _fields(...)`` check above by fabricating
    whatever it is asked for, so this file would pass while proving nothing.
    Assert the module is a real one — the installed package, or the explicit
    fake in tests/sdk_stub.py.
    """
    import sys
    from unittest.mock import MagicMock

    for name in ("claude_agent_sdk", "claude_agent_sdk.types"):
        module = sys.modules.get(name)
        assert module is not None, f"{name} was never imported"
        assert not isinstance(module, MagicMock), (
            f"{name} is a MagicMock — every shape assertion in this file is "
            "vacuous while that is true (#882)"
        )

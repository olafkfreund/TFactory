"""An HONEST stand-in for ``claude_agent_sdk`` when the package is absent (#882).

Why this file exists
--------------------
``tests/conftest.py`` used to drop a ``MagicMock`` into
``sys.modules["claude_agent_sdk"]`` for the whole suite, guarded on
``"claude_agent_sdk" not in sys.modules`` — which is true at conftest import
time whether or not the package is installed. ``claude-agent-sdk`` IS in
``apps/backend/requirements.txt``, so CI and the service venv both had the real
package and both ran every test against a mock of it.

A ``MagicMock`` answers ``hasattr(obj, anything)`` with ``True``. That made the
suite structurally unable to see the shape of an SDK message — which is how
#869 hid: ``usage_from_obj`` read ``getattr(msg, "model", "")`` off a
``ResultMessage`` that has never had a ``.model`` attribute, got ``""`` on every
production run, and stayed green in CI because the repo's own fake set one.

So the mock is now conditional (real package wins), and the fallback for the
genuinely-absent case is this module rather than a ``MagicMock``: an explicit
fake that has exactly the attributes the real types have. A typo or a stale
assumption fails loudly here instead of silently passing.

Scope
-----
Only the names TFactory's own code and tests actually touch. This is a test
stand-in, not a reimplementation. The names and field lists mirror
``claude-agent-sdk`` 0.2.x.

The stub is only ever loaded where the package is absent, so it cannot itself
detect SDK drift. ``tests/test_sdk_shape_contract.py`` is what catches that: it
asserts the same facts against whichever module is loaded, so on the installed
path (CI, the service venv) a shape change goes red there.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

# Evaluated at import, BEFORE anything can put a stand-in into sys.modules, so
# it answers "is the real package installed?" and not "is something importable
# under that name?". Test modules that need the genuine SDK (the MCP `@tool`
# decorator, for instance) skip on this.
SDK_INSTALLED: bool = importlib.util.find_spec("claude_agent_sdk") is not None


# ── content blocks ─────────────────────────────────────────────────────────


@dataclass
class TextBlock:
    text: str


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ""


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: bool | None = None


# ── messages ───────────────────────────────────────────────────────────────


@dataclass
class UserMessage:
    content: Any
    parent_tool_use_id: str | None = None
    tool_use_result: Any = None
    uuid: str | None = None


@dataclass
class AssistantMessage:
    """Carries ``model``: the id that actually served the turn."""

    content: list[Any]
    model: str
    parent_tool_use_id: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
    message_id: str | None = None
    stop_reason: str | None = None
    session_id: str | None = None
    uuid: str | None = None


@dataclass
class ResultMessage:
    """Deliberately has NO ``model`` field, because the real type has none.

    The per-model breakdown lives in ``model_usage``, keyed by model id. This
    absence is the whole point of #882: a stand-in that invents the field turns
    a suite into a mirror of its own assumptions.
    """

    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    stop_reason: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    result: str | None = None
    structured_output: Any = None
    model_usage: dict[str, Any] | None = None
    permission_denials: list[Any] | None = None
    errors: list[str] | None = None
    api_error_status: int | None = None
    uuid: str | None = None
    terminal_reason: str | None = None


@dataclass
class SystemMessage:
    subtype: str
    data: dict[str, Any] = field(default_factory=dict)


Message = Any  # the SDK's union alias; only used in TYPE_CHECKING blocks


# ── client / options ───────────────────────────────────────────────────────


@dataclass
class HookMatcher:
    matcher: str | None = None
    hooks: list[Any] = field(default_factory=list)
    timeout: int | None = None


class ClaudeAgentOptions:
    """Keyword holder mirroring the real options dataclass.

    ponytail: the real type has ~45 fields that only ever get passed in and
    read back. Mirroring them all here would be churn with no reader, so this
    stores what it is given and raises ``AttributeError`` for anything else --
    unlike a MagicMock, it never invents a field that was never set. Give it
    explicit fields if a test ever needs an unset field's default.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        inner = ", ".join(f"{k}={v!r}" for k, v in sorted(self.__dict__.items()))
        return f"ClaudeAgentOptions({inner})"


class ClaudeSDKClient:
    """Constructs and closes; driving it raises rather than returning a mock.

    Every test that actually exercises a session supplies its own client (see
    ``tests/test_resolved_model_evidence.py``), so a stub that silently yielded
    ``MagicMock`` responses could only ever hide a wiring bug.
    """

    def __init__(self, options: Any = None) -> None:
        self.options = options

    def _unavailable(self, name: str) -> RuntimeError:
        return RuntimeError(
            f"ClaudeSDKClient.{name} was called, but claude_agent_sdk is not "
            "installed in this interpreter — this is the test stand-in from "
            "tests/sdk_stub.py. Patch the client in the test, or install "
            "claude-agent-sdk."
        )

    async def connect(self, *a: Any, **k: Any) -> None:
        raise self._unavailable("connect")

    async def disconnect(self, *a: Any, **k: Any) -> None:
        return None

    async def query(self, *a: Any, **k: Any) -> None:
        raise self._unavailable("query")

    async def interrupt(self, *a: Any, **k: Any) -> None:
        raise self._unavailable("interrupt")

    def receive_response(self, *a: Any, **k: Any) -> Any:
        raise self._unavailable("receive_response")

    def receive_messages(self, *a: Any, **k: Any) -> Any:
        raise self._unavailable("receive_messages")

    async def __aenter__(self) -> ClaudeSDKClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class ClaudeSDKError(Exception):
    """Base SDK error."""


class CLINotFoundError(ClaudeSDKError):
    pass


class CLIConnectionError(ClaudeSDKError):
    pass


class ProcessError(ClaudeSDKError):
    pass


def create_sdk_mcp_server(*a: Any, **k: Any) -> dict[str, Any]:
    """Kept present so ``registry.SDK_TOOLS_AVAILABLE`` stays True as before."""
    return {"type": "sdk", "name": k.get("name", "stub"), "tools": k.get("tools", [])}


@dataclass
class SdkMcpTool:
    name: str
    description: str
    input_schema: Any
    handler: Any
    annotations: Any = None


def tool(
    name: str, description: str, input_schema: Any, annotations: Any = None
) -> Any:
    """The ``@tool`` decorator: wraps a handler into an :class:`SdkMcpTool`."""

    def _decorate(handler: Any) -> SdkMcpTool:
        return SdkMcpTool(name, description, input_schema, handler, annotations)

    return _decorate


_EXPORTS = (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    HookMatcher,
    ProcessError,
    ResultMessage,
    SdkMcpTool,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)


def install() -> None:
    """Register the stand-in under ``claude_agent_sdk`` and ``.types``.

    A real ``ModuleType`` rather than a ``MagicMock``: an attribute that was
    never defined raises ``AttributeError``, and ``from claude_agent_sdk import
    Nonsense`` raises ``ImportError``. That is the property the whole issue is
    about.
    """
    root = ModuleType("claude_agent_sdk")
    types_mod = ModuleType("claude_agent_sdk.types")
    for obj in _EXPORTS:
        setattr(root, obj.__name__, obj)
        setattr(types_mod, obj.__name__, obj)
    root.Message = Message
    types_mod.Message = Message
    root.types = types_mod
    root.__tfactory_test_stub__ = True
    types_mod.__tfactory_test_stub__ = True
    sys.modules["claude_agent_sdk"] = root
    sys.modules["claude_agent_sdk.types"] = types_mod

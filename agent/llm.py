"""
LLM adapter Protocol + a FakeLLM for deterministic testing.

Warden's agent loop takes the LLM as a dependency so:
  - CI runs without API keys via FakeLLM.
  - Scenarios can script exact turn-by-turn behavior — a real LLM would
    make the "prompt injection scenario" flakey to assert.
  - Swapping in Anthropic/OpenAI for the demo doesn't change the loop.

The Protocol is intentionally minimal: one method, `turn`, that maps a
message history to either a text response or a tool call. Real LLM
adapters that support parallel tool calls, streaming, etc. can add
capabilities behind their own module — the loop doesn't care.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, Union


@dataclass(frozen=True)
class Message:
    """One turn in the conversation. `role` follows the OpenAI/Anthropic
    convention. `tool_result` is JSON-serializable output from a prior
    tool call, if any."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_result: Any | None = None


@dataclass(frozen=True)
class TextResponse:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """A model-issued tool invocation. In Warden's loop the only tool is
    `retrieve(query: str)`, so `arguments` is `{"query": "..."}`."""

    name: str
    arguments: dict[str, Any]


TurnOutput = Union[TextResponse, ToolCall]


class LLM(Protocol):
    def turn(self, messages: list[Message]) -> TurnOutput: ...


# ---------------------------------------------------------------------------
# FakeLLM — the workhorse for tests and scenarios
# ---------------------------------------------------------------------------

@dataclass
class FakeLLM:
    """An LLM that replays a fixed script of turn outputs.

    `script` is consumed left-to-right. On every `turn()` call, pop the
    next item and return it. If the script runs out, raise — scenarios
    that don't script enough turns are bugs, not "just be quiet."
    """

    script: list[TurnOutput]
    _cursor: int = 0
    # For diagnostics: record what the loop sent us, in order.
    seen_messages: list[list[Message]] = field(default_factory=list)

    def turn(self, messages: list[Message]) -> TurnOutput:
        self.seen_messages.append(list(messages))
        if self._cursor >= len(self.script):
            raise RuntimeError(
                f"FakeLLM script exhausted at turn {self._cursor + 1}. "
                f"Add more entries to the script or reduce the agent's max_turns."
            )
        out = self.script[self._cursor]
        self._cursor += 1
        return out


def script(*items: TurnOutput) -> list[TurnOutput]:
    """Sugar: `script(ToolCall(...), TextResponse(...))` reads nicely at
    scenario call sites."""
    return list(items)


def replay(items: Iterator[TurnOutput]) -> list[TurnOutput]:
    return list(items)

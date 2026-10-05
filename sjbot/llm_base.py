"""Provider-neutral message shapes, so the router never touches a vendor SDK.

Messages the router keeps (plain dicts):
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...], "raw": provider object or None}
  {"role": "tool", "name": str, "result": dict}
An LLM adapter only needs one method:  generate(messages) -> LLMTurn
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class LLMTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None          # the provider's own message object; some providers need it echoed back unchanged

"""Gemini adapter (google-genai SDK). The only file that knows about Gemini; swap it to change providers.

Needs GEMINI_API_KEY in .env. Model comes from SJ_LLM_MODEL (default below), so a newer model is a one-line change.
"""
from __future__ import annotations

import os

from sjbot.guardrails import SYSTEM_PROMPT
from sjbot.llm_base import LLMTurn, ToolCall
from sjbot.tool_schemas import TOOL_DECLARATIONS

DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiChat:
    def __init__(self, model: str | None = None, api_key: str | None = None, temperature: float = 0.2):
        from google import genai
        from google.genai import types

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")
        self._types = types
        self._client = genai.Client(api_key=key)
        self.model = model or os.environ.get("SJ_LLM_MODEL", DEFAULT_MODEL)
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # our router runs the tools
            temperature=temperature,
        )

    def _contents(self, messages: list[dict]) -> list:
        t = self._types
        contents: list = []
        pending: list = []          # consecutive tool results go into ONE user turn

        def flush():
            if pending:
                contents.append(t.Content(role="user", parts=list(pending)))
                pending.clear()

        for m in messages:
            role = m["role"]
            if role == "tool":
                pending.append(t.Part.from_function_response(name=m["name"], response={"result": m["result"]}))
                continue
            flush()
            if role == "user":
                contents.append(t.Content(role="user", parts=[t.Part.from_text(text=m["text"])]))
            elif m.get("raw") is not None:                        # echo the model's own turn back unchanged
                contents.append(m["raw"])
            else:
                parts = [t.Part.from_text(text=m["text"])] if m.get("text") else []
                parts += [t.Part.from_function_call(name=c.name, args=c.args) for c in m.get("tool_calls", [])]
                contents.append(t.Content(role="model", parts=parts))
        flush()
        return contents

    def generate(self, messages: list[dict]) -> LLMTurn:
        resp = self._client.models.generate_content(
            model=self.model, contents=self._contents(messages), config=self._config)
        if not resp.candidates or resp.candidates[0].content is None:
            return LLMTurn()                                      # blocked or empty: the router will fall back
        content = resp.candidates[0].content
        text, calls = [], []
        for part in content.parts or []:
            if getattr(part, "function_call", None):
                calls.append(ToolCall(part.function_call.name, dict(part.function_call.args or {})))
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text.append(part.text)
        return LLMTurn(text="".join(text).strip(), tool_calls=calls, raw=content)

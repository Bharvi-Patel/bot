"""Groq adapter (official `groq` SDK, OpenAI-style chat + tool calling). Same interface as llm_gemini.GeminiChat:
generate(messages) -> LLMTurn. The router, guardrails and tests do not change.

Needs GROQ_API_KEY in .env. Model comes from SJ_LLM_MODEL (default below). Groq's model list changes often, so if the
default is rejected, pick a tool-capable model from https://console.groq.com/docs/models and set SJ_LLM_MODEL.

The router keeps tool results as {"role": "tool", "name", "result"} with no call id, but Groq needs every tool message
to carry the id of the call it answers. We keep the ids in LLMTurn.raw (a list, same order as tool_calls) and hand them
back out, in order, to the tool messages that follow.
"""
from __future__ import annotations

import json
import os

from sjbot.guardrails import SYSTEM_PROMPT
from sjbot.llm_base import LLMTurn, ToolCall
from sjbot.tool_schemas import TOOL_DECLARATIONS

DEFAULT_MODEL = "openai/gpt-oss-120b"
HINT = ("Your last tool call was rejected as invalid. Call the tool again, leaving out every optional parameter "
        "you do not need. Never pass null.")


def _tools_payload(declarations: list[dict]) -> list[dict]:
    out = []
    for d in declarations:
        params = d.get("parameters") or {"type": "object", "properties": {}}   # no-parameter tools still need a schema
        out.append({"type": "function",
                    "function": {"name": d["name"], "description": d["description"], "parameters": params}})
    return out


class GroqChat:
    def __init__(self, model: str | None = None, api_key: str | None = None, temperature: float = 0.2, client=None):
        self.model = model or os.environ.get("SJ_LLM_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.tokens = 0                                          # total tokens used by this object (free tier has a daily cap)
        self._tools = _tools_payload(TOOL_DECLARATIONS)
        if client is not None:                                   # tests inject a fake client
            self._client = client
            return
        from groq import Groq

        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set (add it to .env)")
        self._client = Groq(api_key=key, max_retries=4)          # the SDK backs off on 429 using Groq's retry-after

    def ensure_model(self) -> None:
        """Fail fast, before any question is sent, if this key cannot use the chosen model (no tokens are spent).
        Checks the key's model list; models.retrieve() is not used because it 404s on ids that contain a slash."""
        try:
            ids = sorted(m.id for m in self._client.models.list().data)
        except Exception:
            return                                               # network hiccup etc.: let the real call report it
        if ids and self.model not in ids:
            raise SystemExit(f"Groq model {self.model!r} is not available to this key.\n"
                             f"Set SJ_LLM_MODEL in .env to one of these (pick a chat model, not whisper/tts/guard):\n  "
                             + "\n  ".join(ids))

    def _messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        pending: list[str] = []                                  # call ids waiting for their tool message
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["text"]})
            elif role == "assistant":
                calls = m.get("tool_calls") or []
                msg: dict = {"role": "assistant", "content": m.get("text") or ""}
                if calls:
                    raw = m.get("raw")
                    ids = list(raw) if isinstance(raw, list) and len(raw) == len(calls) else \
                        [f"call_{len(out)}_{i}" for i in range(len(calls))]
                    msg["tool_calls"] = [{"id": i, "type": "function",
                                          "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                                         for i, c in zip(ids, calls)]
                    pending = list(ids)
                out.append(msg)
            elif role == "tool":
                call_id = pending.pop(0) if pending else f"call_orphan_{len(out)}"
                out.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(m["result"], default=str)})
        return out

    def _create(self, payload: list[dict]):
        kw: dict = dict(model=self.model, messages=payload, tools=self._tools, tool_choice="auto",
                        temperature=self.temperature)
        effort = os.environ.get("SJ_REASONING_EFFORT", "low")    # gpt-oss thinks before answering; "low" = faster, far fewer tokens
        if effort and ("gpt-oss" in self.model or "qwen3.8" in self.model):   # models with a reasoning_effort setting
            kw["reasoning_effort"] = effort
        return self._client.chat.completions.create(**kw)

    def generate(self, messages: list[dict]) -> LLMTurn:
        payload = self._messages(messages)
        for attempt in range(3):
            try:
                resp = self._create(payload)
                break
            except Exception as e:
                if "tool_use_failed" not in str(e) or attempt == 2:
                    raise
                # Groq rejected the model's tool call (e.g. an optional parameter sent as null). Say so and retry.
                payload = payload + [{"role": "user", "content": HINT}]
        self.tokens += getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0
        if not resp.choices:
            return LLMTurn()
        msg = resp.choices[0].message
        calls, ids = [], []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            args = {k: v for k, v in args.items() if v is not None} if isinstance(args, dict) else {}   # null = not given
            calls.append(ToolCall(tc.function.name, args))
            ids.append(tc.id)
        return LLMTurn(text=(msg.content or "").strip(), tool_calls=calls, raw=ids or None)
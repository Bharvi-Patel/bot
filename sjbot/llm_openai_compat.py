"""Adapter for any OpenAI-compatible chat API (Mistral, OpenRouter, Together, a local vLLM ...). Same interface as
llm_groq.GroqChat (it subclasses it), so the router, guardrails and tests do not change.

.env, ready-made presets (set SJ_LLM_PROVIDER to the preset name):
  SJ_LLM_PROVIDER=mistral      MISTRAL_API_KEY=...      default model mistral-small-latest
  SJ_LLM_PROVIDER=openrouter   OPENROUTER_API_KEY=...   default model openai/gpt-oss-20b:free
  SJ_LLM_MODEL=...             optional, overrides the preset's default model

Any other OpenAI-style endpoint:
  SJ_LLM_PROVIDER=openai
  SJ_LLM_BASE_URL=https://.../v1
  SJ_LLM_API_KEY=...
  SJ_LLM_MODEL=...

Free-tier limits change often; check the provider's console before a long eval run.
Mistral quirk handled here: its tool-call ids must be exactly 9 letters/digits, so ids are remapped on the way out.
"""
from __future__ import annotations

import hashlib
import os

from sjbot.llm_groq import GroqChat

PRESETS = {
    "mistral": dict(base_url="https://api.mistral.ai/v1", key_env="MISTRAL_API_KEY",
                    model="mistral-small-latest", short_ids=True),
    "openrouter": dict(base_url="https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY",
                       model="openai/gpt-oss-20b:free", short_ids=False),
    "openai": dict(base_url=None, key_env="SJ_LLM_API_KEY", model="gpt-4o-mini", short_ids=False),
}


class OpenAICompatChat(GroqChat):
    def __init__(self, preset: str = "openai", model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, temperature: float = 0.2, client=None):
        if preset not in PRESETS:
            raise RuntimeError(f"Unknown preset {preset!r} (use one of {', '.join(PRESETS)})")
        cfg = PRESETS[preset]
        self.preset, self.short_ids = preset, cfg["short_ids"]
        self.model = model or os.environ.get("SJ_LLM_MODEL", cfg["model"])
        self.temperature = temperature
        self.tokens = 0
        from sjbot.llm_groq import _tools_payload
        from sjbot.tool_schemas import TOOL_DECLARATIONS
        self._tools = _tools_payload(TOOL_DECLARATIONS)
        if client is not None:                                   # tests inject a fake client
            self._client = client
            return
        from openai import OpenAI

        key = api_key or os.environ.get(cfg["key_env"])
        if not key:
            raise RuntimeError(f"{cfg['key_env']} is not set (add it to .env)")
        url = base_url or os.environ.get("SJ_LLM_BASE_URL") or cfg["base_url"]
        if not url:
            raise RuntimeError("SJ_LLM_BASE_URL is not set (add it to .env)")
        self._client = OpenAI(api_key=key, base_url=url, max_retries=4)   # the SDK backs off on 429 / 5xx

    def ensure_model(self) -> None:
        """Warn-free fast check: only fails if the provider lists models and ours is not among them."""
        try:
            ids = sorted(m.id for m in self._client.models.list().data)
        except Exception:
            return                                               # many gateways have no model list: let the real call report it
        if ids and self.model not in ids:
            shown = "\n  ".join(ids[:40]) + (f"\n  ... and {len(ids) - 40} more" if len(ids) > 40 else "")
            raise SystemExit(f"Model {self.model!r} is not available on {self.preset}.\n"
                             f"Set SJ_LLM_MODEL in .env to one of these:\n  {shown}")

    def _messages(self, messages: list[dict]) -> list[dict]:
        out = super()._messages(messages)
        if not self.short_ids:
            return out
        remap: dict[str, str] = {}

        def short(i: str) -> str:                                # same input id -> same 9-char alphanumeric id
            return remap.setdefault(i, hashlib.md5(i.encode()).hexdigest()[:9])

        for m in out:
            for tc in m.get("tool_calls") or []:
                tc["id"] = short(tc["id"])
            if m.get("role") == "tool":
                m["tool_call_id"] = short(m["tool_call_id"])
        return out

    def _create(self, payload: list[dict]):
        # No reasoning_effort here: it is a Groq/OpenAI-specific field that other gateways may reject.
        return self._client.chat.completions.create(model=self.model, messages=payload, tools=self._tools,
                                                    tool_choice="auto", temperature=self.temperature)
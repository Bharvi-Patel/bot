"""Ollama adapter: a model running on this machine (or another one on your network). Same interface as llm_groq.GroqChat:
generate(messages) -> LLMTurn, so the router, guardrails and tests do not change.

.env:
  SJ_LLM_PROVIDER=ollama
  SJ_LLM_MODEL=gpt-oss:20b            (any model you have pulled that supports tools:  ollama pull gpt-oss:20b)
  OLLAMA_HOST=http://localhost:11434   (default)
  SJ_OLLAMA_NUM_CTX=8192               context window sent with every request (see below)
  SJ_OLLAMA_TIMEOUT=300                seconds to wait for one reply; local models can be slow without a GPU
  SJ_OLLAMA_NUM_PREDICT=1024           most tokens one reply may generate (stops a runaway model; 0 = no limit)
  SJ_OLLAMA_KEEP_ALIVE=30m             keep the model loaded between questions (a cold load can take a minute)
  SJ_OLLAMA_THINK=                     off | on | low | medium | high. Default: "low" for gpt-oss, off for every other model
                                       (models like qwen3 otherwise think at length before each answer, which is very slow locally).

Why this talks to Ollama's own /api/chat and not its OpenAI-compatible /v1 endpoint: only /api/chat lets every request set
num_ctx. Our system prompt plus the tool definitions already use roughly 2,500 tokens; with a small default context window
Ollama would silently cut off the start of the prompt and the bot would behave strangely, with no error.
"""
from __future__ import annotations

import json
import os

import httpx

from sjbot.guardrails import SYSTEM_PROMPT
from sjbot.llm_base import LLMTurn, ToolCall
from sjbot.llm_groq import HINT, _tools_payload
from sjbot.tool_schemas import TOOL_DECLARATIONS

DEFAULT_MODEL = "gpt-oss:20b"


class OllamaChat:
    def __init__(self, model: str | None = None, host: str | None = None, temperature: float = 0.2, client: httpx.Client | None = None):
        self.model = model or os.environ.get("SJ_LLM_MODEL", DEFAULT_MODEL)
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.temperature = temperature
        self.num_ctx = int(os.environ.get("SJ_OLLAMA_NUM_CTX", "8192"))
        self.keep_alive = os.environ.get("SJ_OLLAMA_KEEP_ALIVE", "30m")
        self.num_predict = int(os.environ.get("SJ_OLLAMA_NUM_PREDICT", "1024"))
        self.timeout = float(os.environ.get("SJ_OLLAMA_TIMEOUT", "300"))
        self.tokens = 0
        self._think_unsupported = False                          # set once a model says it has no thinking setting
        self._tools = _tools_payload(TOOL_DECLARATIONS)
        self._client = client or httpx.Client(timeout=self.timeout)

    # ---------------------------------------------------------------- startup check
    def ensure_model(self) -> None:
        """Fail fast, with the fix in the message, if Ollama is not running or the model has not been pulled."""
        try:
            resp = self._client.get(f"{self.host}/api/tags")
            resp.raise_for_status()
            names = sorted(m.get("name", "") for m in resp.json().get("models", []))
        except (httpx.ConnectError, httpx.TimeoutException):
            raise SystemExit(f"Ollama is not running at {self.host}. Start the Ollama app (or run `ollama serve`), then try again.")
        except Exception:
            return                                               # something odd: let the real call report it
        have = {self.model, self.model + ":latest"}
        if names and not (have & set(names)):
            raise SystemExit(f"Ollama does not have the model {self.model!r}.\nPull it with:  ollama pull {self.model}\n"
                             "or set SJ_LLM_MODEL in .env to one you already have:\n  " + "\n  ".join(names))

    # ---------------------------------------------------------------- request
    def _messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in messages:
            role = m["role"]
            if role == "user":
                out.append({"role": "user", "content": m["text"]})
            elif role == "assistant":
                msg: dict = {"role": "assistant", "content": m.get("text") or ""}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{"function": {"name": c.name, "arguments": c.args}} for c in m["tool_calls"]]
                out.append(msg)
            elif role == "tool":
                out.append({"role": "tool", "tool_name": m["name"], "content": json.dumps(m["result"], default=str)})
        return out

    def _think(self) -> bool | str | None:
        if self._think_unsupported:
            return None
        setting = os.environ.get("SJ_OLLAMA_THINK", "").strip().lower()
        if setting in ("off", "false", "no"):
            return False
        if setting in ("on", "true", "yes"):
            return True
        if setting in ("low", "medium", "high"):
            return setting
        if "gpt-oss" in self.model:                              # gpt-oss thinks before answering; "low" is faster and far cheaper
            return os.environ.get("SJ_REASONING_EFFORT", "low") or None
        return False                                             # everything else: no thinking (a model without the setting is retried bare)

    def _payload(self, messages: list[dict], think: bool | str | None) -> dict:
        options: dict = {"temperature": self.temperature, "num_ctx": self.num_ctx}
        if self.num_predict > 0:
            options["num_predict"] = self.num_predict
        body: dict = {"model": self.model, "messages": messages, "tools": self._tools, "stream": False,
                      "keep_alive": self.keep_alive, "options": options}
        if think is not None:
            body["think"] = think
        return body

    def _post(self, body: dict) -> dict:
        resp = self._client.post(f"{self.host}/api/chat", json=body)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"{resp.status_code}: {resp.text[:400]}", request=resp.request, response=resp)
        return resp.json()

    def generate(self, messages: list[dict]) -> LLMTurn:
        payload = self._messages(messages)
        think = self._think()
        data: dict = {}
        for attempt in range(3):
            try:
                data = self._post(self._payload(payload, think))
                break
            except httpx.TimeoutException as e:
                raise RuntimeError(
                    f"Ollama did not answer within {self.timeout:g} s (model {self.model}). The model is probably running on the CPU or "
                    "still loading. Check `ollama ps` (the PROCESSOR column should say GPU), try a smaller model, or raise SJ_OLLAMA_TIMEOUT.") from e
            except httpx.HTTPStatusError as e:
                text = str(e).lower()
                if think is not None and "think" in text:        # this model has no thinking setting: ask again without it, and remember
                    think = None
                    self._think_unsupported = True
                    continue
                if attempt < 2 and any(w in text for w in ("tool", "parsing", "invalid")):
                    payload = payload + [{"role": "user", "content": HINT}]   # the model produced a broken tool call: say so, retry
                    continue
                raise
        self.tokens += int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0)
        msg = data.get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            args = {k: v for k, v in args.items() if v is not None} if isinstance(args, dict) else {}   # null = not given
            if fn.get("name"):
                calls.append(ToolCall(fn["name"], args))
        return LLMTurn(text=(msg.get("content") or "").strip(), tool_calls=calls, raw=None)
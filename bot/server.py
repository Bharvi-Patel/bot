"""HTTP service for the bot. Called by mw/API from the server side only, never by a browser.

    BOT_SERVICE_KEY=<secret> uvicorn --factory bot.server:create_app --host 127.0.0.1 --port 8100

POST /chat
  Headers (set by the backend from the real request and session; they are what the bot trusts):
    X-Bot-Key        the shared service key (BOT_SERVICE_KEY). Without it, or with a wrong one: 401.
    Store-UUID       the store of the storefront that sent the chat.
    X-Client-Id      who is asking, for rate limits: the real client IP or a signed session id.
    X-Customer-Uuid  only for a really logged-in customer. Leave it out for visitors.
    X-Customer-Token that customer's own login token (the one the store API issued), only when logged in. The bot forwards it to the
                     store API for order questions; the API decides which orders are theirs. It is never logged or shown.
  Body (JSON, nothing else is accepted):
    {"message": "...", "history": [{"role": "user" | "assistant", "text": "..."}]}
  Reply: {"reply": "...", "blocked": null | "<reason>"}

Limits (in-process, so run ONE worker; see bot/service_limits.py): BOT_RATE_PER_CLIENT (default 20 chats a minute for one
X-Client-Id in one store), BOT_RATE_PER_STORE (default 300 a minute), BOT_MAX_CONCURRENT (default 8 chats at once) and
BOT_MAX_BODY_BYTES (default 16384). Over a rate limit: 429, full: 503, both with Retry-After; too large: 413.

If BOT_STORE_CONFIG_DIR is not set every chat would run with the South Jackson development settings, so the service
refuses chats (503) unless BOT_ALLOW_DEV_DEFAULTS=1 is set on purpose, for local work.

Logging: every POST /chat writes one line to the "bot.access" logger (key=value, one line, never any message, history,
reply, raw client id or customer id): request_id, status, ms, store, client (the same 12-character hash Langfuse uses for the
visitor), logged_in, blocked, tools. The caller may send X-Request-Id (letters, digits . _ - up to 64) to follow one chat
across mw and the bot; otherwise one is made. It is returned in the X-Request-Id response header.

Who is asking and which store are never read from the body or from anything the model can write: the body is
rejected if it carries any field besides message and history. If BOT_SERVICE_KEY is not set the service refuses
every chat (fail closed).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from bot import stores
from bot.context import ChatContext
from bot.service_limits import ConcurrencyGate, SlidingWindowLimiter
from bot.store_config import valid_store_id

log = logging.getLogger(__name__)
access_log = logging.getLogger("bot.access")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,64}")
_TOOL_NAME = re.compile(r"[a-z_]{1,40}")

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY = 10
_CLIENT_ID = re.compile(r"[A-Za-z0-9._:\-]{1,128}")
_CUSTOMER_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{16,4096}")          # what a JWT is made of; no spaces or line breaks


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    text: str = Field(max_length=MAX_MESSAGE_CHARS * 2)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a body that names a store or a customer is refused
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryItem] = Field(default_factory=list, max_length=MAX_HISTORY)


class ChatResponse(BaseModel):
    reply: str
    blocked: str | None = None


@dataclass(frozen=True)
class Limits:
    per_client: int = 20          # chats a minute for one visitor (X-Client-Id) in one store
    per_store: int = 300          # chats a minute for a whole store
    max_concurrent: int = 8       # chats running at once, so a burst cannot use up the model quota
    max_body_bytes: int = 16384

    @classmethod
    def from_env(cls) -> "Limits":
        def num(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or not raw.strip():
                return default
            if not raw.strip().isdigit() or int(raw) < 1:
                raise ValueError(f"{name} must be a whole number of 1 or more, got {raw!r}")
            return int(raw)
        return cls(num("BOT_RATE_PER_CLIENT", cls.per_client), num("BOT_RATE_PER_STORE", cls.per_store),
                   num("BOT_MAX_CONCURRENT", cls.max_concurrent), num("BOT_MAX_BODY_BYTES", cls.max_body_bytes))


class _BodyLimit:
    """Answers 413 when a request body is larger than the limit, whether it announces its size or is sent in chunks.
    For chunks the 413 is sent the moment the limit is crossed and the app is told the client went away; whatever the
    app then tries to send is dropped (FastAPI would otherwise turn a read error into a 400)."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            return await self._refuse(send)
        seen = 0
        refused = False

        async def counting_receive():
            nonlocal seen, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    refused = True
                    await self._refuse(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not refused:
                await send(message)

        await self.app(scope, counting_receive, guarded_send)

    async def _refuse(self, send):
        body = json.dumps({"detail": "request body is too large"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _logfmt(value: Any) -> str:
    """One safe token: no spaces, quotes or line breaks, so a value can never split or forge a log line."""
    text = re.sub(r"[^A-Za-z0-9._:,\-]", "?", str(value))[:100]
    return text or "-"


class _AccessLog:
    """One log line per POST /chat, whatever its outcome (401, 413, 429, 503, 502 ... included). The handler leaves the
    facts in scope["state"]; nothing from the message, the history or the reply is ever read here."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/chat" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        state = scope.setdefault("state", {})
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else secrets.token_hex(8)
        status = 500

        async def tracking_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        finally:
            try:
                tools = [t for t in (state.get("tools") or []) if isinstance(t, str) and _TOOL_NAME.fullmatch(t)]
                line = (f"chat request_id={_logfmt(request_id)} status={status} ms={int((time.perf_counter() - started) * 1000)} "
                        f"store={_logfmt(state.get('store') or '-')} client={_logfmt(state.get('client') or '-')} "
                        f"logged_in={'yes' if state.get('logged_in') else 'no'} blocked={_logfmt(state.get('blocked') or '-')} "
                        f"tools={','.join(tools) or '-'}")
                level = logging.WARNING if status >= 500 or status in (401, 403) else logging.INFO
                access_log.log(level, line)
            except Exception:                                   # a logging problem must never break a chat
                log.debug("access log failed", exc_info=True)


def create_app(answer_fn: Callable[..., dict] | None = None, llm_factory: Callable[[], Any] | None = None,
               service_key: str | None = None, limits: Limits | None = None) -> FastAPI:
    app = FastAPI(title="store chat bot", docs_url=None, redoc_url=None, openapi_url=None)
    key = service_key if service_key is not None else os.getenv("BOT_SERVICE_KEY", "")
    limits = limits or Limits.from_env()
    app.add_middleware(_BodyLimit, max_bytes=limits.max_body_bytes)
    app.add_middleware(_AccessLog)                       # added last = outermost: it sees what the body limit answers too
    access_log.setLevel(logging.INFO)
    if not logging.getLogger().handlers and not access_log.handlers:       # uvicorn only configures its own loggers
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        access_log.addHandler(handler)
    client_limiter = SlidingWindowLimiter(limits.per_client)
    store_limiter = SlidingWindowLimiter(limits.per_store)
    app.state.gate = ConcurrencyGate(limits.max_concurrent)
    state: dict[str, Any] = {}
    llm_lock = threading.Lock()

    def llm():
        with llm_lock:                                   # two first chats at once must not build two model clients
            if "llm" not in state:
                factory = llm_factory
                if factory is None:
                    from bot.llm import get_llm as factory
                state["llm"] = factory()
            return state["llm"]

    def trusted_context(
        request: Request,
        x_bot_key: str | None = Header(default=None),
        store_uuid: str | None = Header(default=None),
        x_client_id: str | None = Header(default=None),
        x_customer_uuid: str | None = Header(default=None),
        x_customer_token: str | None = Header(default=None),
    ) -> ChatContext:
        if not key:
            raise HTTPException(503, "chat is not configured")
        if not x_bot_key or not hmac.compare_digest(x_bot_key.encode(), key.encode()):
            raise HTTPException(401, "unauthorized")
        if not stores.is_multi_store() and os.getenv("BOT_ALLOW_DEV_DEFAULTS") != "1":
            log.error("BOT_STORE_CONFIG_DIR is not set: refusing chats (set BOT_ALLOW_DEV_DEFAULTS=1 only for local work)")
            raise HTTPException(503, "chat is not configured")
        if not valid_store_id(store_uuid):
            raise HTTPException(400, "Store-UUID header is missing or not valid")
        if not x_client_id or not _CLIENT_ID.fullmatch(x_client_id):
            raise HTTPException(400, "X-Client-Id header is missing or not valid")
        if x_customer_uuid is not None and not valid_store_id(x_customer_uuid):
            raise HTTPException(400, "X-Customer-Uuid header is not valid")
        if x_customer_token is not None and not _CUSTOMER_TOKEN.fullmatch(x_customer_token):
            raise HTTPException(400, "X-Customer-Token header is not valid")          # the value is never echoed or logged
        request.state.store = store_uuid
        request.state.client = hashlib.sha256(x_client_id.encode()).hexdigest()[:12]      # same short hash as the Langfuse tag
        request.state.logged_in = bool(x_customer_uuid or x_customer_token)
        return ChatContext(store_uuid=store_uuid, client_id=x_client_id, customer_uuid=x_customer_uuid or None,
                           customer_token=x_customer_token or None)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request, ctx: ChatContext = Depends(trusted_context)) -> ChatResponse:
        fn = answer_fn
        if fn is None:
            from bot.router import answer as fn
        history = [{"role": h.role, "text": h.text} for h in body.history]
        for limiter, rate_key in ((client_limiter, f"{ctx.store_uuid}|{ctx.client_id}"), (store_limiter, str(ctx.store_uuid))):
            allowed, wait = limiter.allow(rate_key)
            if not allowed:
                raise HTTPException(429, "too many messages, try again shortly", headers={"Retry-After": str(wait)})
        if not app.state.gate.try_enter():
            raise HTTPException(503, "the assistant is busy, try again shortly", headers={"Retry-After": "2"})
        try:
            out = fn(body.message, llm(), history, ctx=ctx)
        except Exception:
            log.exception("chat failed")                 # no message text in the log
            raise HTTPException(502, "the assistant is unavailable right now") from None
        finally:
            app.state.gate.leave()
        request.state.blocked = out.get("blocked")
        request.state.tools = out.get("tools_used")
        return ChatResponse(reply=out["reply"], blocked=out.get("blocked"))

    return app

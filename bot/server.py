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

Funnel events (bot/events.py): when BOT_EVENTS_DIR is set, every chat turn that reaches the assistant also writes one
event line (counts, tool names and outcome; never any customer text). The caller may send X-Conversation-Id (letters,
digits . _ - up to 64) so turns of one conversation can be grouped; without it the field is empty.

POST /webhooks/ringg  (Ringg.AI call events; see bot/ringg.py)
  Ringg posts here directly, so it does not use X-Bot-Key: it sends X-Webhook-Secret (RINGG_WEBHOOK_SECRET), which you also put in
  the agent's Event Subscription headers. The store is looked up from the event's agent id in RINGG_AGENT_STORES, never read
  from the payload (a store the agent is not allowed, or one with no enabled settings file, is dropped). Bodies up to 1 MB are accepted here (transcripts); /chat keeps its small limit. Each accepted call event
  becomes one "call_event" line in the same funnel log as chat turns: ids, status, duration, cost and callback flag only; no
  phone numbers, transcript, summary or recording link. No secret configured: 503; wrong secret: 401; not an event: 400.

POST /ringg/tools/<tool>  (the Ringg.AI voice agent's API tools; see bot/ringg.py)
  Tools: search_products, get_product_details, recommend_products, search_policies, list_categories, list_brands,
  list_attribute_values, get_shipping_options, get_store_info, check_inventory. Never get_order_status: a caller is a visitor.
  Headers set in the agent's API tool: X-Ringg-Tools-Key (RINGG_TOOLS_KEY), X-Ringg-Agent-Id (store from RINGG_AGENT_STORES),
  Store-UUID (the store; required for a shared agent), X-Ringg-Call-Id (optional). RINGG_AGENT_STORES may map an agent to
  "*": it then serves any store that has an enabled settings file (BOT_STORE_CONFIG_DIR is required) and must always send
  Store-UUID. Body: the tool's arguments. Reply: the tool's result. Each call writes a "call_tool" event line.
  The chat reply checks (guardrails.check_reply, price repair) do not apply: Ringg's own model writes what is spoken.

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
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from bot import events, ringg, stores
from bot.context import ChatContext
from bot.service_limits import ConcurrencyGate, SlidingWindowLimiter
from bot.store_config import StoreNotConfigured, service_key_from_env, service_key_ok, valid_store_id

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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


RINGG_PATH = "/webhooks/ringg"
RINGG_TOOLS_PREFIX = "/ringg/tools/"
RINGG_MAX_BODY_BYTES = 1_048_576          # a completed call carries its transcript; 1 MB is far above a long call


class _BodyLimit:
    """Answers 413 when a request body is larger than the limit, whether it announces its size or is sent in chunks.
    For chunks the 413 is sent the moment the limit is crossed and the app is told the client went away; whatever the
    app then tries to send is dropped (FastAPI would otherwise turn a read error into a 400)."""

    def __init__(self, app, max_bytes: int, per_path: dict[str, int] | None = None):
        self.app, self.max_bytes, self.per_path = app, max_bytes, per_path or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.per_path.get(scope.get("path", ""), self.max_bytes)      # e.g. call events carry whole transcripts
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
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
                if seen > limit:
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
        state["request_id"] = request_id                 # the funnel event carries the same id as the access-log line
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
               service_key: str | dict[str, str] | None = None, limits: Limits | None = None,
               events_sink: "events.EventSink | None" = None, ringg_secret: str | None = None,
               ringg_agent_stores: dict[str, Any] | None = None, ringg_tools_key: str | dict[str, str] | None = None,
               tool_fn: Callable[..., dict] | None = None) -> FastAPI:
    app = FastAPI(title="store chat bot", docs_url=None, redoc_url=None, openapi_url=None)
    key = service_key if service_key is not None else service_key_from_env()
    limits = limits or Limits.from_env()
    sink = events_sink if events_sink is not None else events.sink_from_env()
    app.add_middleware(_BodyLimit, max_bytes=limits.max_body_bytes, per_path={RINGG_PATH: RINGG_MAX_BODY_BYTES})
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
    webhook_secret = ringg_secret if ringg_secret is not None else ringg.secret_from_env()
    agent_stores = ringg_agent_stores if ringg_agent_stores is not None else ringg.agent_stores_from_env()
    seen_calls = ringg.Seen()
    tools_key = ringg_tools_key if ringg_tools_key is not None else ringg.tools_key_from_env()
    if any(v == ringg.WILDCARD for v in agent_stores.values()) and not stores.is_multi_store():
        log.warning("RINGG_AGENT_STORES has a \"*\" agent but BOT_STORE_CONFIG_DIR is not set: that agent is refused until it is set")

    def store_served(store: str, wildcard: bool) -> bool:
        """Whether a Ringg call or event may be answered for this store. With a settings folder: only a store with an
        enabled settings file. Without one (local development): any store the agent's list allows, but never a \"*\" agent,
        because then nothing would limit which store it names."""
        if not stores.is_multi_store():
            return not wildcard
        try:
            stores.config_for(ChatContext(store_uuid=store))
        except StoreNotConfigured:
            return False
        return True

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
        x_conversation_id: str | None = Header(default=None),
    ) -> ChatContext:
        if not key:
            raise HTTPException(503, "chat is not configured")
        if not service_key_ok(x_bot_key, store_uuid, key):
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
        request.state.conversation = x_conversation_id if x_conversation_id and _REQUEST_ID.fullmatch(x_conversation_id) else None
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
        started = time.perf_counter()
        outcome, blocked_reason = "error", None
        with events.collect() as tool_events:
            try:
                out = fn(body.message, llm(), history, ctx=ctx)
                blocked_reason = out.get("blocked")
                outcome = "blocked" if blocked_reason else "answered"
            except Exception:
                log.exception("chat failed")             # no message text in the log
                raise HTTPException(502, "the assistant is unavailable right now") from None
            finally:
                app.state.gate.leave()
                _emit_event(request, ctx, outcome, blocked_reason, started, tool_events)
        request.state.blocked = out.get("blocked")
        request.state.tools = out.get("tools_used")
        return ChatResponse(reply=out["reply"], blocked=out.get("blocked"))

    def _emit_event(request: Request, ctx: ChatContext, outcome: str, blocked_reason: Any, started: float, tool_events: list) -> None:
        try:
            st = request.state
            sink.write(events.build_chat_event(
                request_id=getattr(st, "request_id", None), store=ctx.store_uuid, client=getattr(st, "client", None),
                conversation=getattr(st, "conversation", None), logged_in=bool(getattr(st, "logged_in", False)),
                outcome=outcome, blocked=blocked_reason, ms=int((time.perf_counter() - started) * 1000), tool_events=list(tool_events)))
        except Exception:                                # an event problem must never reach the customer
            log.debug("funnel event failed", exc_info=True)

    @app.post(RINGG_PATH)
    async def ringg_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)) -> dict[str, Any]:
        """Ringg.AI call events. Not called through mw: Ringg posts here directly, so it has its own secret, not X-Bot-Key.
        The secret is checked before the body is read. Always 2xx once accepted (Ringg retries anything else), 400 only
        for a body that is not a Ringg event (Ringg treats a 4xx as final and does not retry it)."""
        if not webhook_secret:
            raise HTTPException(503, "call events are not configured")
        if not ringg.secret_ok(x_webhook_secret, webhook_secret):
            raise HTTPException(401, "unauthorized")
        try:
            payload = json.loads(await request.body())
        except ValueError:
            raise HTTPException(400, "body is not JSON") from None
        what, event = ringg.handle(payload, agent_stores, seen_calls, store_served=store_served)
        if what == "bad_payload":
            raise HTTPException(400, "not a call event")
        if event is not None:
            try:
                sink.write(event)
            except Exception:                            # an event problem must never turn into a retry storm
                log.debug("call event failed", exc_info=True)
        log.info("ringg_event result=%s type=%s", what, payload.get("event_type") if isinstance(payload.get("event_type"), str) and ringg._CODE.fullmatch(payload["event_type"]) else "other")
        return {"received": True}

    @app.post(RINGG_TOOLS_PREFIX + "{tool_name}")
    async def ringg_tool(tool_name: str, request: Request, x_ringg_tools_key: str | None = Header(default=None),
                         x_ringg_agent_id: str | None = Header(default=None),
                         x_ringg_call_id: str | None = Header(default=None),
                         store_uuid: str | None = Header(default=None),
                         x_store_uuid: str | None = Header(default=None)) -> dict[str, Any]:
        """A store tool, called by the Ringg.AI voice agent while it talks. Same tools as /chat, run by the same code
        (guardrails.run_tool), with a visitor's context: never a customer, so orders are not available here.
        Headers (all set in the agent's API tool in Ringg, none by the caller's voice):
          X-Ringg-Tools-Key  static value, must equal RINGG_TOOLS_KEY (401 otherwise, 503 if unset).
          X-Ringg-Agent-Id   which agent is calling; checked against RINGG_AGENT_STORES (403 if unknown).
          Store-UUID / X-Store-Uuid  optional Variable row from store_uuid for a shared multi-store agent; must be one of the stores RINGG_AGENT_STORES allows for this agent (403 otherwise). Without it, an agent with one store uses that store. An agent mapped to "*" must always send it, and the store must have an enabled settings file (403 "store is not configured" otherwise).
          X-Ringg-Call-Id    optional, the call id (a Variable row from call_data.id): rate limit and funnel link.
        Body: the tool's arguments as JSON ({} or empty for list_* tools). The reply is the tool's result."""
        if not tools_key:
            raise HTTPException(503, "voice tools are not configured")
        if not ringg.tools_key_ok(x_ringg_tools_key, x_ringg_agent_id, tools_key):
            raise HTTPException(401, "unauthorized")
        if tool_name not in ringg.VOICE_TOOLS:
            raise HTTPException(404, "no such tool")
        if not x_ringg_agent_id or (agent_stores and x_ringg_agent_id not in agent_stores):
            raise HTTPException(403, "unknown agent")
        call_id = x_ringg_call_id if x_ringg_call_id and _REQUEST_ID.fullmatch(x_ringg_call_id) else None
        raw = await request.body()
        try:
            args = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise HTTPException(400, "body is not JSON") from None
        if not isinstance(args, dict):
            raise HTTPException(400, "body must be a JSON object")
        explicit_store = store_uuid or x_store_uuid          # headers only: the body is written by the Ringg model, never trusted for the store
        store = ringg.resolve_store(x_ringg_agent_id, agent_stores, explicit_store)
        if not store:
            raise HTTPException(403, "unknown agent")
        ctx = ChatContext(store_uuid=store, client_id=f"ringg:{call_id or x_ringg_agent_id}"[:128])     # a visitor: no customer id or token
        if not store_served(store, ringg.is_wildcard(agent_stores, x_ringg_agent_id)):
            raise HTTPException(403, "store is not configured")
        args = ringg.clean_tool_args(args)
        for limiter, rate_key in ((client_limiter, f"{store}|ringg:{call_id or x_ringg_agent_id}"), (store_limiter, store)):
            allowed, wait = limiter.allow(rate_key)
            if not allowed:
                raise HTTPException(429, "too many requests, try again shortly", headers={"Retry-After": str(wait)})
        if not app.state.gate.try_enter():
            raise HTTPException(503, "busy, try again shortly", headers={"Retry-After": "2"})
        fn = tool_fn
        if fn is None:
            from bot.guardrails import run_tool as fn
        started = time.perf_counter()
        try:
            result = await run_in_threadpool(fn, tool_name, args, ctx)
        finally:
            app.state.gate.leave()
        try:
            sink.write(ringg.build_tool_event(store, call_id, events.summarize_tool(tool_name, args, result),
                                              int((time.perf_counter() - started) * 1000)))
        except Exception:
            log.debug("call tool event failed", exc_info=True)
        return result if isinstance(result, dict) else {"error": "tool_failed"}

    return app

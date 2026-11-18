"""HTTP service for the bot. Called by mw/API from the server side only, never by a browser.

    BOT_SERVICE_KEY=<secret> uvicorn --factory bot.server:create_app --host 127.0.0.1 --port 8100

POST /chat
  Headers (set by the backend from the real request and session; they are what the bot trusts):
    X-Bot-Key        the shared service key (BOT_SERVICE_KEY). Without it, or with a wrong one: 401.
    Store-UUID       the store of the storefront that sent the chat.
    X-Client-Id      who is asking, for rate limits: the real client IP or a signed session id.
    X-Customer-Uuid  only for a really logged-in customer. Leave it out for visitors.
  Body (JSON, nothing else is accepted):
    {"message": "...", "history": [{"role": "user" | "assistant", "text": "..."}]}
  Reply: {"reply": "...", "blocked": null | "<reason>"}

Who is asking and which store are never read from the body or from anything the model can write: the body is
rejected if it carries any field besides message and history. If BOT_SERVICE_KEY is not set the service refuses
every chat (fail closed).
"""
from __future__ import annotations

import hmac
import logging
import os
import re
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from bot.context import ChatContext
from bot.store_config import valid_store_id

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1000
MAX_HISTORY = 10
_CLIENT_ID = re.compile(r"[A-Za-z0-9._:\-]{1,128}")


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


def create_app(answer_fn: Callable[..., dict] | None = None, llm_factory: Callable[[], Any] | None = None,
               service_key: str | None = None) -> FastAPI:
    app = FastAPI(title="store chat bot", docs_url=None, redoc_url=None, openapi_url=None)
    key = service_key if service_key is not None else os.getenv("BOT_SERVICE_KEY", "")
    state: dict[str, Any] = {}

    def llm():
        if "llm" not in state:
            factory = llm_factory
            if factory is None:
                from bot.llm import get_llm as factory
            state["llm"] = factory()
        return state["llm"]

    def trusted_context(
        x_bot_key: str | None = Header(default=None),
        store_uuid: str | None = Header(default=None),
        x_client_id: str | None = Header(default=None),
        x_customer_uuid: str | None = Header(default=None),
    ) -> ChatContext:
        if not key:
            raise HTTPException(503, "chat is not configured")
        if not x_bot_key or not hmac.compare_digest(x_bot_key.encode(), key.encode()):
            raise HTTPException(401, "unauthorized")
        if not valid_store_id(store_uuid):
            raise HTTPException(400, "Store-UUID header is missing or not valid")
        if not x_client_id or not _CLIENT_ID.fullmatch(x_client_id):
            raise HTTPException(400, "X-Client-Id header is missing or not valid")
        if x_customer_uuid is not None and not valid_store_id(x_customer_uuid):
            raise HTTPException(400, "X-Customer-Uuid header is not valid")
        return ChatContext(store_uuid=store_uuid, client_id=x_client_id, customer_uuid=x_customer_uuid or None)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, ctx: ChatContext = Depends(trusted_context)) -> ChatResponse:
        fn = answer_fn
        if fn is None:
            from bot.router import answer as fn
        history = [{"role": h.role, "text": h.text} for h in body.history]
        try:
            out = fn(body.message, llm(), history, ctx=ctx)
        except Exception:
            log.exception("chat failed")                 # no message text in the log
            raise HTTPException(502, "the assistant is unavailable right now") from None
        return ChatResponse(reply=out["reply"], blocked=out.get("blocked"))

    return app

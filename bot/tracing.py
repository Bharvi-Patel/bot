"""Optional tracing to a self-hosted Langfuse. Does nothing unless it is switched on, and can never break the bot.

On when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_HOST points at your server, e.g. http://localhost:3000).
Off when SJ_TRACING=off, when the langfuse package is missing, and while pytest runs.

Every value is masked (emails, phone numbers, long order numbers) BEFORE it leaves the process. Traces hold the customer
message, the tool arguments and results, the model's draft and the reply check, so keep the Langfuse screen private.
"""
from __future__ import annotations

import atexit
import hashlib
import logging
import os
import re
import sys
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}")
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.\-\u2011]{0,2}\d{3}[\s.\-\u2011]\d{4}\b")
_LONG_NUMBER = re.compile(r"\b\d{9,}\b")                     # order numbers look like 102500001
_SECRET_KEYS = {"email", "billing_email", "password", "api_key", "authorization", "customer_uuid", "client_id"}


def mask(data: Any) -> Any:
    """Remove personal data from anything about to be sent to the trace server."""
    if isinstance(data, str):
        return _LONG_NUMBER.sub("[number]", _PHONE.sub("[phone]", _EMAIL.sub("[email]", data)))
    if isinstance(data, dict):
        return {k: ("[redacted]" if str(k).lower() in _SECRET_KEYS and v else mask(v)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask(v) for v in data]
    return data


class _Null:
    """Stands in for a trace object when tracing is off or a tracing call failed."""
    def update(self, **kw: Any) -> None:
        pass


class _Safe:
    def __init__(self, obs: Any):
        self._obs = obs

    def update(self, **kw: Any) -> None:
        try:
            self._obs.update(**kw)
        except Exception:
            log.debug("trace update failed", exc_info=True)


_NULL = _Null()
_client: Any = None


def _init() -> None:
    global _client
    if os.environ.get("SJ_TRACING", "").lower() == "off" or "pytest" in sys.modules:
        return
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return
    try:
        from langfuse import Langfuse
        _client = Langfuse(mask=mask)
        atexit.register(flush)
        log.info("tracing on (host %s)", os.environ.get("LANGFUSE_HOST", "default"))
    except Exception:
        log.warning("tracing could not start; continuing without it", exc_info=True)
        _client = None


def enabled() -> bool:
    return _client is not None


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            log.debug("trace flush failed", exc_info=True)


@contextmanager
def span(name: str, *, input: Any = None, as_type: str = "span", **kw: Any):
    """with tracing.span("tool:x", input=args) as sp: ...; sp.update(output=...). A no-op when tracing is off."""
    cm, obs = None, _NULL
    if _client is not None:
        try:
            cm = _client.start_as_current_observation(as_type=as_type, name=name, input=input, **kw)
            obs = _Safe(cm.__enter__())
        except Exception:
            log.debug("trace start failed", exc_info=True)
            cm, obs = None, _NULL
    try:
        yield obs
    except BaseException as exc:
        if cm is not None:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
        raise
    else:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


@contextmanager
def visitor(ctx: Any):
    """Tag everything inside with a short hash of the visitor id (never the raw IP or customer id).
    Uses the SDK's propagate_attributes; if the installed SDK does not have it, tagging is skipped."""
    cm = None
    if _client is not None:
        try:
            from langfuse import propagate_attributes
            cid = getattr(ctx, "client_id", None)
            attrs: dict[str, Any] = {"metadata": {"logged_in": "true" if getattr(ctx, "customer_uuid", None) else "false"}}
            if cid:
                attrs["user_id"] = hashlib.sha256(cid.encode()).hexdigest()[:12]
            cm = propagate_attributes(**attrs)
            cm.__enter__()
        except Exception:
            log.debug("trace tagging failed", exc_info=True)
            cm = None
    try:
        yield
    except BaseException as exc:
        if cm is not None:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
        raise
    else:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


def _view(messages: list[dict]) -> list[dict]:
    """A readable copy of the conversation: no provider objects, nothing the SDK could not serialise."""
    out = []
    for m in messages:
        item = {"role": m.get("role")}
        for key in ("text", "name", "result"):
            if m.get(key) not in (None, ""):
                item[key] = m[key]
        if m.get("tool_calls"):
            item["tool_calls"] = [{"name": c.name, "args": c.args} for c in m["tool_calls"]]
        out.append(item)
    return out


def generate(llm: Any, messages: list[dict]):
    """llm.generate(messages) as a traced generation, with the tokens it used."""
    if _client is None:
        return llm.generate(messages)
    before = getattr(llm, "tokens", 0) or 0
    with span("llm", as_type="generation", input=_view(messages), model=getattr(llm, "model", None)) as gen:
        turn = llm.generate(messages)
        used = (getattr(llm, "tokens", 0) or 0) - before
        gen.update(output={"text": turn.text, "tool_calls": [{"name": c.name, "args": c.args} for c in turn.tool_calls]},
                   **({"usage_details": {"total": used}} if used > 0 else {}))
        return turn


_init()
"""Ringg.AI call events -> the same funnel event log as the chat (bot/events.py).

Ringg POSTs one JSON event per call stage to POST /webhooks/ringg (bot/server.py). This module holds everything that is not
HTTP: checking the shared secret, finding the store, ignoring repeats, and turning a Ringg payload into one event line.

What is kept (fields checked one by one, like chat events): the call id, the id of the call it came from, the store, the
conversation id when the call was started with one, the Ringg event type, call type, status, sub-status, duration, cost,
whether a callback was asked for, and Ringg's one-word classification.
What is NEVER kept: phone numbers (to_number, from_number, called_on...), the transcript, the summary and key points,
custom_args_values other than the conversation id, the recording URL, tool_call_logs. Those are customer data; storing any
of them would need its own consent and retention decision, the same rule bot/events.py follows for chat text.

Which store a call belongs to is never read from the payload. It comes from RINGG_AGENT_STORES, a JSON object that maps a
Ringg agent id to a store id, e.g. {"<agent id>": "<store uuid>"}. An event from an agent that is not in the map is
acknowledged and dropped (a 4xx would make Ringg retry nothing useful, and the event is not ours to record).

Tools the voice agent may call (POST /ringg/tools/<name>): the same store tools the chat uses, minus orders. A phone or browser
caller is never logged in, so get_order_status is not on the list at all (not merely "returns please log in"). Ringg's own model
writes the spoken reply, so the chat reply checks in bot/guardrails.py do NOT run on voice: the tools' live prices and the agent's
prompt are the protection (docs/ringg setup notes in bot/server.py).

Setup (see bot/server.py): RINGG_WEBHOOK_SECRET is the value you put in the agent's Event Subscription headers as
"X-Webhook-Secret"; RINGG_AGENT_STORES as above. Without the secret the endpoint refuses everything (fail closed).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from bot.events import SCHEMA_VERSION, _CODE, _ID, _count

log = logging.getLogger(__name__)

# The Ringg event types we record. Anything else (call_ringing, call_ongoing, chat_completed...) is acknowledged and ignored.
RECORDED = ("call_started", "call_completed", "platform_analysis_completed", "all_processing_completed")
_CALL_TYPES = ("inbound", "outbound", "webcall")
_STATUSES = ("registered", "ongoing", "retry", "completed", "failed", "error", "cancelled", "forwarded")
_SUB = re.compile(r"[a-z_ ]{1,40}")
_CLASS = re.compile(r"[a-z0-9_]{1,40}")
MAX_SEEN = 20_000


def secret_from_env() -> str:
    return os.getenv("RINGG_WEBHOOK_SECRET", "").strip()


def agent_stores_from_env() -> dict[str, str]:
    """RINGG_AGENT_STORES: {"<ringg agent id>": "<store uuid>"}. Entries with an unusable id are refused at start-up."""
    raw = os.getenv("RINGG_AGENT_STORES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"RINGG_AGENT_STORES must be a JSON object: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("RINGG_AGENT_STORES must be a JSON object of agent id -> store id")
    for agent, store in data.items():
        if not isinstance(agent, str) or not _ID.fullmatch(agent) or not isinstance(store, str) or not _ID.fullmatch(store):
            raise ValueError("RINGG_AGENT_STORES has an agent id or store id with characters that are not allowed")
    return dict(data)


def tools_key_from_env() -> str:
    """RINGG_TOOLS_KEY: the value of the static header the agent's API tools send (X-Ringg-Tools-Key). Unset = tools refuse."""
    return os.getenv("RINGG_TOOLS_KEY", "").strip()


def secret_ok(received: str | None, expected: str) -> bool:
    """Constant-time check of the X-Webhook-Secret header. An unset expected secret never matches."""
    return bool(expected) and bool(received) and hmac.compare_digest(received.encode(), expected.encode())


class Seen:
    """Remembers (call_id, event_type) pairs, because Ringg retries and can deliver the same event twice.
    In memory and bounded, so it is per process and forgets on restart: the reader of the log must also keep one line
    per (call, ringg_event). That is cheap, since the pair is on every line."""

    def __init__(self, limit: int = MAX_SEEN):
        self._items: OrderedDict[str, None] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def first_time(self, key: str) -> bool:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return False
            self._items[key] = None
            if len(self._items) > self._limit:
                self._items.popitem(last=False)
            return True


VOICE_TOOLS = ("search_products", "get_product_details", "recommend_products", "search_policies", "list_categories", "list_brands",
               "list_attribute_values", "get_shipping_options", "get_store_info", "check_inventory")
assert "get_order_status" not in VOICE_TOOLS

_NUM_ARGS = ("min_price", "max_price", "max_width_in")
_INT_ARGS = ("limit",)
_BLANK_WORDS = frozenset({
    "", "null", "none", "nil", "n/a", "na", "any", "all", "unknown",
    "undefined", "unspecified", "not specified", "not_specified",
    "default", "-", "--", "*", "false", "0", "empty", "no",
})


def clean_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    """Ringg sends every configured Dynamic Row in the JSON body, often with "", "null", "any", "N/A", "none" or 0 for
    optional rows the model did not need, and sometimes sends integer/decimal rows as strings ("500") or sort as
    "name_asc". Drop blank/placeholder optional rows, coerce numeric strings, and resolve mutually-exclusive rows
    before handing the dict to the store tools."""
    from bot.tools.search_products import ATTRIBUTE_GROUPS

    out: dict[str, Any] = {}
    for k, v in args.items():
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            if k in _NUM_ARGS or k in _INT_ARGS:
                if v <= 0:
                    continue
                out[k] = int(v) if k in _INT_ARGS else v
            else:
                out[k] = v
            continue
        if isinstance(v, str):
            s = v.strip()
            if s.lower() in _BLANK_WORDS:
                continue
            if k in _INT_ARGS:
                if re.fullmatch(r"-?\d+", s):
                    val = int(s)
                    if val > 0:
                        out[k] = val
                continue
            if k in _NUM_ARGS:
                if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
                    val_f = float(s) if "." in s else int(s)
                    if val_f > 0:
                        out[k] = val_f
                continue
            out[k] = s
            continue
        out[k] = v

    if "sort" in out and out["sort"] not in ("price_asc", "price_desc"):
        out.pop("sort", None)

    if "parent_slug" in out and "keyword" in out:
        out.pop("parent_slug", None)

    group = out.get("attribute_group")
    valid_groups = {g.lower() for g in ATTRIBUTE_GROUPS}
    if not group or not out.get("attribute_value") or not isinstance(group, str) or group.lower() not in valid_groups:
        out.pop("attribute_group", None)
        out.pop("attribute_value", None)

    if "color" in out:
        out.pop("material", None)
        out.pop("attribute_group", None)
        out.pop("attribute_value", None)
    elif "material" in out:
        out.pop("attribute_group", None)
        out.pop("attribute_value", None)

    if not out.get("keyword") and not out.get("brand_slug"):
        out.pop("color", None)
        out.pop("material", None)
        out.pop("attribute_group", None)
        out.pop("attribute_value", None)

    out.pop("store_uuid", None)
    out.pop("store_id", None)
    return out


def resolve_store(agent_id: str | None, agent_stores: dict[str, str], explicit_store: Any = None) -> str | None:
    """Resolve the store for a Ringg call or tool request.
    1. Explicit store_uuid passed from the storefront via Ringg custom variables / header (Store-UUID / X-Store-Uuid / custom_args_values.store_uuid).
    2. Fallback to RINGG_AGENT_STORES[agent_id] (for dedicated per-store agents or dashboard tests).
    3. Fallback to BOT_STORE_UUID when the agent is in RINGG_AGENT_STORES or for single-store dev setups."""
    candidate = _id(explicit_store.strip() if isinstance(explicit_store, str) else explicit_store)
    if candidate and candidate.lower() not in _BLANK_WORDS:
        return candidate
    if isinstance(agent_id, str) and agent_id in agent_stores:
        return agent_stores[agent_id]
    return None


def build_tool_event(store: str, call_id: str | None, summary: dict, ms: int, now: datetime | None = None) -> dict[str, Any]:
    """One line for a tool the voice agent ran: what kind of search, how many results, never the words or the products.
    `summary` is bot.events.summarize_tool(...), which already holds only counts, flags and catalog slugs."""
    now = now or datetime.now(timezone.utc)
    return {
        "v": SCHEMA_VERSION,
        "event": "call_tool",
        "ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "store": store if isinstance(store, str) and _ID.fullmatch(store) else None,
        "call_id": _id(call_id),
        "ms": _count(ms),
        "tool": summary if isinstance(summary, dict) else {"name": "other", "ok": False},
    }


def _id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _number(value: Any, top: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= top:
        return None
    return round(float(value), 2)


def _analysis(payload: dict) -> dict:
    """platform_analysis_completed has it as analysis_data; all_processing_completed as platform_analysis."""
    data = payload.get("analysis_data") if payload.get("event_type") == "platform_analysis_completed" else payload.get("platform_analysis")
    return data if isinstance(data, dict) else {}


def build_call_event(payload: dict, store: str, now: datetime | None = None) -> dict[str, Any]:
    """One call event line. Every field is checked, so a bad value is dropped, never written as given."""
    now = now or datetime.now(timezone.utc)
    args = payload.get("custom_args_values")
    conversation = _id(args.get("conversation_id")) if isinstance(args, dict) else None
    analysis = _analysis(payload)
    classification = analysis.get("classification")
    sub = payload.get("sub_status")
    return {
        "v": SCHEMA_VERSION,
        "event": "call_event",
        "ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "store": store if isinstance(store, str) and _ID.fullmatch(store) else None,
        "call_id": _id(payload.get("call_id")),
        "parent_call_id": _id(payload.get("parent_call_id")),
        "conversation": conversation,
        "ringg_event": payload.get("event_type") if payload.get("event_type") in RECORDED else None,
        "call_type": payload.get("call_type") if payload.get("call_type") in _CALL_TYPES else None,
        "status": payload.get("status") if payload.get("status") in _STATUSES else None,
        "sub_status": sub.lower() if isinstance(sub, str) and _SUB.fullmatch(sub.lower()) else None,
        "duration_s": _number(payload.get("call_duration"), 86_400),
        "cost": _number(payload.get("call_cost"), 10_000),
        "callback_requested": analysis.get("callback_requested") if isinstance(analysis.get("callback_requested"), bool) else None,
        "classification": classification if isinstance(classification, str) and _CLASS.fullmatch(classification) else None,
        "agent_messages": _count(payload.get("agent_message_count")),
        "user_messages": _count(payload.get("user_message_count")),
    }


def handle(payload: Any, agent_stores: dict[str, str], seen: Seen, now: datetime | None = None) -> tuple[str, dict | None]:
    """(what happened, the event to write or None). What happened is one of:
    bad_payload, ignored_type, unknown_agent, duplicate, recorded. The caller answers 400 for bad_payload, 2xx for the rest."""
    if not isinstance(payload, dict) or not _id(payload.get("call_id")) or not isinstance(payload.get("event_type"), str):
        return "bad_payload", None
    if payload["event_type"] not in RECORDED:
        return "ignored_type", None
    agent_id = payload.get("agent_id") if isinstance(payload.get("agent_id"), str) else None
    if not agent_id or (agent_stores and agent_id not in agent_stores):
        return "unknown_agent", None
    custom_args = payload.get("custom_args_values")
    explicit_store = (custom_args.get("store_uuid") or custom_args.get("store_id")) if isinstance(custom_args, dict) else None
    store = resolve_store(agent_id, agent_stores, explicit_store)
    if not store:
        return "unknown_agent", None
    if not seen.first_time(f"{payload['call_id']}:{payload['event_type']}"):
        return "duplicate", None
    return "recorded", build_call_event(payload, store, now)


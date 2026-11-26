"""Guardrails: the tool allowlist and dispatcher, the default system prompt, and the store's trusted contact text.

The tools already validate their own arguments (allowed keys, caps, error dicts), so the dispatcher stays thin:
it only decides WHICH tools exist, calls them, and makes sure a crash never leaks details to the model.
Tools in CTX_TOOLS also get the request context (who is asking). That context is built by the server, never by the model.

The rest of the guardrails live in their own modules and are re-exported here, so `from bot.guardrails import ...` keeps working:
  bot/gates.py        checks made before the model sees a message (extraction attempts, order changes)
  bot/nudges.py       one-time second chances for the model, the holiday-hours reply, brand grounding
  bot/reply_check.py  the last check on a reply, and the repairs for prices, contacts and links
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from bot.context import ChatContext
from bot.dev_defaults import SOUTH_JACKSON
from bot.prompt import extraction_reply, render_system_prompt
from bot.tools.check_inventory import check_inventory
from bot.tools.get_order_status import get_order_status
from bot.tools.get_product_details import get_product_details
from bot.tools.get_shipping_options import get_shipping_options
from bot.tools.get_store_info import get_store_info
from bot.tools.list_attribute_values import list_attribute_values
from bot.tools.list_brands import list_brands
from bot.tools.list_categories import list_categories
from bot.tools.recommend_products import recommend_products
from bot.tools.search_policies import search_policies
from bot.tools.search_products import search_products

log = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_TURN = 5   # the router loop stops after this many calls in one customer message

TOOLS = {
    "search_products": search_products,
    "get_product_details": get_product_details,
    "recommend_products": recommend_products,
    "search_policies": search_policies,
    "list_categories": list_categories,
    "list_brands": list_brands,
    "list_attribute_values": list_attribute_values,
    "get_shipping_options": get_shipping_options,
    "get_store_info": get_store_info,
    "check_inventory": check_inventory,
    "get_order_status": get_order_status,
}
CTX_TOOLS = {"get_order_status", "get_store_info", "list_brands", "list_categories", "get_product_details", "search_policies", "search_products",
             "list_attribute_values", "recommend_products", "get_shipping_options"}   # called as fn(args, ctx=ctx)

# Page text written by people can name another store or an old number, so contact details found inside it are not
# trusted: a reply may only repeat phones/emails from other tools or from the store record (get_store_info).
CONTACT_UNTRUSTED_TOOLS = {"search_policies", "get_shipping_options"}

TOOL_FAILED = {"error": "tool_failed",
               "note": "Do not guess. Point the customer to the store phone or email (get_store_info)."}


def run_tool(name: str, args: Any, ctx=None) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        log.warning("model asked for unknown tool %r", name)
        return {"error": "unknown_tool"}
    if not isinstance(args, dict):
        return {"error": "arguments must be a JSON object"}
    try:
        result = fn(args, ctx=ctx) if name in CTX_TOOLS else fn(args)
    except Exception:
        log.exception("tool %s crashed", name)
        return dict(TOOL_FAILED)
    log.info("tool=%s args=%s ok=%s", name, sorted(args), "error" not in result)   # names only, no values
    return result


# Default prompt and reply: South Jackson, for local development and the existing tests. Each chat on a multi-store
# server gets its own, built from that store's settings: render_system_prompt(cfg) and extraction_reply(cfg).
SYSTEM_PROMPT = render_system_prompt(SOUTH_JACKSON)

FALLBACK = "Sorry, I can't answer that reliably. Please contact the store directly."
EXTRACTION_REPLY = extraction_reply(SOUTH_JACKSON)


_STORE_CONTACT_TEXT: str | None = None     # cached get_store_info result; "" = unavailable (tests set this)


_CONTACT_BY_STORE: dict[str, tuple[float, str]] = {}
CONTACT_TTL = 300.0                        # seconds; each store's contact details are cached separately


def store_contact_text(store_uuid: str | None = None) -> str:
    """The store's own phone/email as text. Our code fetches it (not the model) and caches it, so a reply may repeat
    the store's real contact details even when the model did not call get_store_info this turn.
    With a store_uuid the text is that store's and is cached per store for CONTACT_TTL seconds. Without one (local
    development) a single process-wide copy is kept."""
    global _STORE_CONTACT_TEXT
    if store_uuid:
        hit = _CONTACT_BY_STORE.get(store_uuid)
        if hit and time.monotonic() - hit[0] < CONTACT_TTL:
            return hit[1]
        try:
            info = TOOLS["get_store_info"]({}, ctx=ChatContext(store_uuid=store_uuid))
            if not isinstance(info, dict) or info.get("error") or info.get("found") is False:
                return ""                  # unavailable: not cached, so the next call tries again
            text = json.dumps(info, default=str, ensure_ascii=False)
        except Exception:
            log.exception("could not load store contact details")
            return ""
        _CONTACT_BY_STORE[store_uuid] = (time.monotonic(), text)
        return text
    if _STORE_CONTACT_TEXT is None:
        try:
            info = TOOLS["get_store_info"]({})
            if not isinstance(info, dict) or info.get("error") or info.get("found") is False:
                return ""  # unavailable or empty: do not cache it, so the next call tries again
            _STORE_CONTACT_TEXT = json.dumps(info, default=str, ensure_ascii=False)
        except Exception:
            log.exception("could not load store contact details")
            return ""
    return _STORE_CONTACT_TEXT


# Re-exported so callers and tests can keep importing these from bot.guardrails.
from bot.gates import ORDER_CHANGE_REPLY, is_extraction_attempt, is_order_change_request  # noqa: E402,F401
from bot.nudges import (BRAND_ONLY_NOTE, CHEAPEST_NUDGE, HOURS_NUDGE, KEYWORD_NUDGE, POLICY_FIRST_NUDGE,  # noqa: E402,F401
                        POLICY_NUDGE, PRODUCT_RETRY_NUDGE, PRODUCT_SEARCH_TOOLS, UNCATEGORIZED_NUDGE, WEAK_POLICY_MATCH,
                        _HOURS_QUESTION, _is_bare_pointer, add_no_info_prefix, declined_without_policy_search,
                        declined_without_product_search, ground_brand_search, ground_brand_search_ex, holiday_hours_reply,
                        is_bare_contact_reply, needs_fresh_price_search, required_tool_nudge, superlative_search_lacks_keyword)
from bot.reply_check import (check_reply, repair_contacts, repair_rounded_prices, repair_unverified_prices,  # noqa: E402,F401
                             strip_invented_budget, strip_tool_name_links, strip_unknown_links, unknown_links, unverified_quotes)

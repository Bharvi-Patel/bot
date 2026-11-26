"""The Phase 5 router: customer message -> model <-> tools loop -> checked reply.

No separate intent classifier and no free-form SQL (data map section 6): the model picks from the tools in
guardrails.TOOLS, we run them, and the final text must pass check_reply before it is sent.
"""
from __future__ import annotations

import json
import logging
import os
import time
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from bot import events, tracing
from bot.context import ChatContext
from bot.gates import ORDER_CHANGE_REPLY, is_extraction_attempt, is_order_change_request
from bot.guardrails import CONTACT_UNTRUSTED_TOOLS, FALLBACK, MAX_TOOL_CALLS_PER_TURN, SYSTEM_PROMPT, run_tool, store_contact_text
from bot.nudges import (BRAND_ONLY_NOTE, CHEAPEST_NUDGE, POLICY_NUDGE, add_no_info_prefix, declined_without_policy_search,
                        declined_without_product_search, ground_brand_search_ex, holiday_hours_reply, is_bare_contact_reply,
                        needs_fresh_price_search, required_tool_nudge, superlative_search_lacks_keyword)
from bot.prompt import extraction_reply, render_system_prompt
from bot.reply_check import check_reply, repair_contacts, repair_unverified_prices, strip_tool_name_links, strip_unknown_links
from bot.store_config import StoreNotConfigured
from bot.stores import UNAVAILABLE_REPLY, config_for
from bot.tool_schemas import VISITOR_TOOL_DECLARATIONS

log = logging.getLogger(__name__)

MAX_USER_CHARS = 1000
MAX_HISTORY_MESSAGES = 10
REVIEW_LOG = Path(__file__).resolve().parents[1] / "logs" / "review.jsonl"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
FINAL_ANSWER_NUDGE = ("No more tool calls are allowed. Using only the tool results above, write your final answer to the customer now. "
                      "If the results do not answer the question, say you could not find it and give the store's phone number.")
_LIMIT_RESULT = {"error": "tool_call_limit",
                 "note": "No more tool calls are allowed for this message. Answer from what you already have, "
                         "or say you could not find it and give the store's phone or email."}


def _clean_history(history: list[dict] | None) -> list[dict]:
    """Text-only turns from earlier in the conversation. Anything else (tool results, odd roles) is dropped."""
    out = []
    for m in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str) and m["text"].strip():
            out.append({"role": m["role"], "text": m["text"][:MAX_USER_CHARS * 2]})
    return out


def log_for_review(question: str, reason: str, tools_used: list[str]) -> None:
    """Questions the bot could not answer well, for local/eval review. Disabled during HTTP service turns unless
    BOT_LOG_CUSTOMER_TEXT=1 is explicitly set, so customer chat text is never persisted by default."""
    if os.getenv("BOT_REVIEW_LOG") == "0":
        return
    if events.is_collecting() and os.getenv("BOT_LOG_CUSTOMER_TEXT") != "1":
        return
    try:
        REVIEW_LOG.parent.mkdir(exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason,
               "tools": tools_used, "question": _EMAIL.sub("[email]", question)[:300]}
        with REVIEW_LOG.open("a", encoding="utf8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        log.exception("could not write review log")


_BRANDS_BY_STORE: dict[str, tuple[float, list]] = {}    # store id -> (when loaded, brands)
BRANDS_TTL = 300.0


def known_brands(store_uuid: str | None = None) -> list:
    """Brands the store carries, from that store's API, kept per store for BRANDS_TTL seconds. One store's brands
    are never used for another. Without a store_uuid the BOT_STORE_UUID setting is used (local development and
    single-store setups); with neither, there are no brands. A failed lookup returns [] and is retried next time;
    it must never break a customer's search."""
    store_uuid = store_uuid or os.getenv("BOT_STORE_UUID")
    if not store_uuid:
        return []
    hit = _BRANDS_BY_STORE.get(store_uuid)
    if hit and time.monotonic() - hit[0] < BRANDS_TTL:
        return hit[1]
    try:
        from bot.api_client import get_client
        rows = get_client(ChatContext(store_uuid=store_uuid)).brands()
        brands = [{"slug": b["slug"], "name": b["name"]} for b in rows if b.get("slug") and b.get("name")]
    except Exception:
        return []
    _BRANDS_BY_STORE[store_uuid] = (time.monotonic(), brands)
    return brands


def _first_nudge(nudges: list, used: set[str], text: str) -> tuple[str, str | None, str] | None:
    """The first nudge not yet used this turn whose check applies: (name, review-log reason, instruction), or None."""
    for name, review_reason, check in nudges:
        if name not in used and (instruction := check(text)):
            return name, review_reason, instruction
    return None


def _answer(user_message: str, llm, history: list[dict] | None = None, max_calls: int = MAX_TOOL_CALLS_PER_TURN,
           ctx=None) -> dict:
    """Returns {"reply": str, "tools_used": [names], "blocked": reason or None}.
    ctx (bot.context.ChatContext) says who is asking; your server builds it from the request, never from the message."""
    try:
        cfg = config_for(ctx)                           # this chat's store: its name, domains and rules
    except StoreNotConfigured as exc:                   # unknown or switched-off store: no model call at all
        log.warning("chat refused: %s", exc)
        return {"reply": UNAVAILABLE_REPLY, "tools_used": [], "blocked": "store_not_configured"}
    store_uuid = getattr(ctx, "store_uuid", None)
    prompt = render_system_prompt(cfg)
    system = None if prompt == SYSTEM_PROMPT else prompt   # only a store whose prompt differs sends its own

    question = (user_message or "").strip()[:MAX_USER_CHARS]
    if not question:
        return {"reply": "How can I help you today?", "tools_used": [], "blocked": None}

    if is_extraction_attempt(question):                 # asks for our prompt, tools or database: the model never sees it
        log_for_review(question, "extraction_attempt", [])
        return {"reply": extraction_reply(cfg), "tools_used": [], "blocked": "extraction_attempt"}

    if is_order_change_request(question):               # cancel / change / refund an order: fixed reply, the model never sees it
        log_for_review(question, "order_change_request", [])
        return {"reply": ORDER_CHANGE_REPLY, "tools_used": [], "blocked": "order_change_request"}

    messages = _clean_history(history) + [{"role": "user", "text": question}]
    user_said = " ".join(m["text"] for m in messages if m["role"] == "user")   # figures the customer typed earlier may be repeated
    earlier_replies = " ".join(m["text"] for m in messages if m["role"] == "assistant")   # prices we already told them
    results: list[dict] = []
    contact_results: list[dict] = []                     # results whose phones/emails the reply may repeat
    tools_used: list[str] = []
    reply: str | None = None
    reason: str | None = None
    search_calls: list[dict] = []                       # arguments of the product searches that really ran
    seen_calls: dict[str, dict] = {}                    # identical repeat calls in one turn are answered from here and cost nothing
    # When the model answers without a tool call, these are tried in order and the first that applies sends it back once
    # with its instruction. Each: (name, review-log reason or None, check(reply) -> instruction or None).
    nudges: list[tuple[str, str | None, Callable[[str], str | None]]] = [
        ("fresh_price", None,                            # answering "cheapest" from memory: make it search
         lambda text: CHEAPEST_NUDGE if needs_fresh_price_search(question, tools_used) else None),
        ("keyword", "superlative_keyword_retried",       # cheapest-first list with no type word
         lambda text: superlative_search_lacks_keyword(question, search_calls)),
        ("required_tool", "required_tool_retried",       # categories / brands / policies / hours from memory
         lambda text: required_tool_nudge(question, tools_used)),
        ("policy_first", "declined_without_policy_search_retried",      # "don't have it" without searching the store's pages
         lambda text: declined_without_policy_search(text, question, tools_used)),
        ("product_retry", "declined_without_product_search_retried",    # misspelt product word went to the policy search
         lambda text: declined_without_product_search(text, question, tools_used, results)),
        ("bare_contact", "bare_contact_reply_retried",   # found the answer, then only gave the phone number
         lambda text: POLICY_NUDGE if is_bare_contact_reply(text, results, question, tools_used) else None),
    ]
    nudges_used: set[str] = set()

    tool_decls = None if getattr(ctx, "customer_token", None) else VISITOR_TOOL_DECLARATIONS

    for _ in range(max_calls + 2):                      # room for the limit message and one last answer
        try:
            turn = tracing.generate(llm, messages, system, tools=tool_decls)
        except Exception:
            log.exception("llm call failed")
            reason = "llm_error"
            break
        if not turn.tool_calls:
            nudge = _first_nudge(nudges, nudges_used, turn.text)
            if nudge is None:
                reply = turn.text
                break
            name, review_reason, instruction = nudge
            nudges_used.add(name)
            if review_reason:
                log_for_review(question, review_reason, tools_used)
            messages.append({"role": "assistant", "text": turn.text})
            messages.append({"role": "user", "text": instruction})
            continue
        messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls, "raw": turn.raw})
        for call in turn.tool_calls:
            call_key = json.dumps([call.name, call.args], sort_keys=True, default=str)
            if call_key in seen_calls:                  # the model asked the same thing again: reuse the answer, do not spend a call
                result = {**seen_calls[call_key], "repeat_note": "You already made this exact call; this is the same result. Use it or try something different."}
            elif len(tools_used) >= max_calls:
                result = dict(_LIMIT_RESULT)
            else:
                args, brand_only = call.args, False
                if call.name == "search_products":
                    args, brand_only = ground_brand_search_ex(call.args, question, known_brands(store_uuid))
                    if args != call.args:
                        log_for_review(question, "brand_search_grounded", tools_used)
                with tracing.span(f"tool:{call.name}", input=args) as sp:
                    result = run_tool(call.name, args, ctx)
                    if brand_only and isinstance(result, dict):
                        result = {**result, "customer_request": BRAND_ONLY_NOTE}
                    sp.update(output=result)
                tools_used.append(call.name)
                results.append(result)
                events.record_tool(call.name, args, result)          # funnel event summary; a no-op outside the HTTP service
                seen_calls[call_key] = result
                if call.name == "search_products":
                    search_calls.append(args)
                if call.name not in CONTACT_UNTRUSTED_TOOLS:
                    contact_results.append(result)
            messages.append({"role": "tool", "name": call.name, "result": result})

    if reply is None and reason is None and tools_used:                # ran out of turns still calling tools: ask once for an answer, no tools
        messages.append({"role": "user", "text": FINAL_ANSWER_NUDGE})
        try:
            last = tracing.generate(llm, messages, system, tools=tool_decls)
            if not last.tool_calls and (last.text or "").strip():
                reply = last.text
        except Exception:
            log.exception("final answer call failed")
    blocked_reply = None                                               # the reply the safety check threw away, kept for the eval log
    if reply is None and reason is None:
        reason = "no_final_answer"
    if reply is not None and not reply.strip():
        reply, reason = None, "empty_reply"
    if reply is not None and (holiday := holiday_hours_reply(question, results)):   # holidays are not in the store data: never let the model guess
        log_for_review(question, "holiday_hours_replaced", tools_used)
        reply = holiday
    if reply is not None:
        trusted = store_contact_text(store_uuid)
        with tracing.span("reply_check", input=reply) as sp:
            ok, why = check_reply(reply, results, user_said, trusted, contact_results, earlier_replies, cfg.allowed_domains)
            sp.update(output={"ok": ok, "reason": why})
        repairs: dict[str, Callable[[str], str | None]] = {             # which repair fixes which failed check
            "unknown_link": lambda r: strip_unknown_links(r, results, question, cfg.allowed_domains),
            "unverified_price": lambda r: repair_unverified_prices(r, results, user_said, earlier_replies),
            "internal_leak": strip_tool_name_links,
            "unknown_email": lambda r: repair_contacts(r, contact_results, trusted),
            "unknown_phone": lambda r: repair_contacts(r, contact_results, trusted),
        }
        for _ in range(2):                                             # right answer, bad contact line or link: fix it
            if ok or why not in repairs:
                break
            fixed = repairs[why](reply)
            if not fixed or fixed == reply:
                break
            log.warning("repaired reply (%s)", why)
            log_for_review(question, f"repaired_{why}", tools_used)
            reply = fixed
            ok, why = check_reply(reply, results, user_said, trusted, contact_results, earlier_replies, cfg.allowed_domains)
        if not ok:
            blocked_reply = reply
            reply, reason = None, why
        else:
            reply = add_no_info_prefix(reply, results, question, tools_used)

    if reply is None:
        log_for_review(question, reason or "unknown", tools_used)
        return {"reply": FALLBACK, "tools_used": tools_used, "blocked": reason, "blocked_reply": blocked_reply}
    if any(isinstance(r, dict) and (r.get("found") is False or r.get("error")) for r in results):
        log_for_review(question, "tool_found_nothing_or_error", tools_used)
    return {"reply": reply, "tools_used": tools_used, "blocked": None}


def answer(user_message: str, llm, history: list[dict] | None = None, max_calls: int = MAX_TOOL_CALLS_PER_TURN,
           ctx=None) -> dict:
    """Same as before; the whole turn is also one trace when tracing is on (see bot/tracing.py)."""
    with tracing.visitor(ctx), tracing.span("chat_turn", input=str(user_message)[:MAX_USER_CHARS]) as sp:
        out = _answer(user_message, llm, history, max_calls, ctx)
        sp.update(output={"reply": out["reply"], "tools_used": out["tools_used"], "blocked": out["blocked"]})
        return out
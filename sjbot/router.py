"""The Phase 5 router: customer message -> model <-> tools loop -> checked reply.

No separate intent classifier and no free-form SQL (data map section 6): the model picks from the tools in
guardrails.TOOLS, we run them, and the final text must pass check_reply before it is sent.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sjbot import tracing
from sjbot.guardrails import (CONTACT_UNTRUSTED_TOOLS, EXTRACTION_REPLY, FALLBACK, ORDER_CHANGE_REPLY, MAX_TOOL_CALLS_PER_TURN, add_no_info_prefix, CHEAPEST_NUDGE, check_reply, needs_fresh_price_search, is_extraction_attempt, is_order_change_request,
                              repair_contacts, run_tool, store_contact_text, strip_unknown_links)

log = logging.getLogger(__name__)

MAX_USER_CHARS = 1000
MAX_HISTORY_MESSAGES = 10
REVIEW_LOG = Path(__file__).resolve().parents[1] / "logs" / "review.jsonl"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
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
    """Questions the bot could not answer well, for the weekly review. Emails are removed; best effort only."""
    try:
        REVIEW_LOG.parent.mkdir(exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason,
               "tools": tools_used, "question": _EMAIL.sub("[email]", question)[:300]}
        with REVIEW_LOG.open("a", encoding="utf8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        log.exception("could not write review log")


def _answer(user_message: str, llm, history: list[dict] | None = None, max_calls: int = MAX_TOOL_CALLS_PER_TURN,
           ctx=None) -> dict:
    """Returns {"reply": str, "tools_used": [names], "blocked": reason or None}.
    ctx (sjbot.context.ChatContext) says who is asking; your server builds it from the request, never from the message."""
    question = (user_message or "").strip()[:MAX_USER_CHARS]
    if not question:
        return {"reply": "How can I help you today?", "tools_used": [], "blocked": None}

    if is_extraction_attempt(question):                 # asks for our prompt, tools or database: the model never sees it
        log_for_review(question, "extraction_attempt", [])
        return {"reply": EXTRACTION_REPLY, "tools_used": [], "blocked": "extraction_attempt"}

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
    nudged = False

    for _ in range(max_calls + 2):                      # room for the limit message and one last answer
        try:
            turn = tracing.generate(llm, messages)
        except Exception:
            log.exception("llm call failed")
            reason = "llm_error"
            break
        if not turn.tool_calls:
            if not nudged and needs_fresh_price_search(question, tools_used):      # answering "cheapest" from memory: make it search
                nudged = True
                messages.append({"role": "assistant", "text": turn.text})
                messages.append({"role": "user", "text": CHEAPEST_NUDGE})
                continue
            reply = turn.text
            break
        messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls, "raw": turn.raw})
        for call in turn.tool_calls:
            if len(tools_used) >= max_calls:
                result = dict(_LIMIT_RESULT)
            else:
                with tracing.span(f"tool:{call.name}", input=call.args) as sp:
                    result = run_tool(call.name, call.args, ctx)
                    sp.update(output=result)
                tools_used.append(call.name)
                results.append(result)
                if call.name not in CONTACT_UNTRUSTED_TOOLS:
                    contact_results.append(result)
            messages.append({"role": "tool", "name": call.name, "result": result})

    if reply is None and reason is None:
        reason = "no_final_answer"
    if reply is not None and not reply.strip():
        reply, reason = None, "empty_reply"
    if reply is not None:
        trusted = store_contact_text()
        with tracing.span("reply_check", input=reply) as sp:
            ok, why = check_reply(reply, results, user_said, trusted, contact_results, earlier_replies)
            sp.update(output={"ok": ok, "reason": why})
        for _ in range(2):                                             # right answer, bad contact line or link: fix it
            if ok or why not in ("unknown_email", "unknown_phone", "unknown_link"):
                break
            fixed = (strip_unknown_links(reply, results, question) if why == "unknown_link"
                     else repair_contacts(reply, contact_results, trusted))
            if not fixed or fixed == reply:
                break
            log.warning("repaired reply (%s)", why)
            log_for_review(question, f"repaired_{why}", tools_used)
            reply = fixed
            ok, why = check_reply(reply, results, user_said, trusted, contact_results, earlier_replies)
        if not ok:
            reply, reason = None, why
        else:
            reply = add_no_info_prefix(reply, results, question, tools_used)

    if reply is None:
        log_for_review(question, reason or "unknown", tools_used)
        return {"reply": FALLBACK, "tools_used": tools_used, "blocked": reason}
    if any(isinstance(r, dict) and (r.get("found") is False or r.get("error")) for r in results):
        log_for_review(question, "tool_found_nothing_or_error", tools_used)
    return {"reply": reply, "tools_used": tools_used, "blocked": None}


def answer(user_message: str, llm, history: list[dict] | None = None, max_calls: int = MAX_TOOL_CALLS_PER_TURN,
           ctx=None) -> dict:
    """Same as before; the whole turn is also one trace when tracing is on (see sjbot/tracing.py)."""
    with tracing.visitor(ctx), tracing.span("chat_turn", input=str(user_message)[:MAX_USER_CHARS]) as sp:
        out = _answer(user_message, llm, history, max_calls, ctx)
        sp.update(output={"reply": out["reply"], "tools_used": out["tools_used"], "blocked": out["blocked"]})
        return out
"""get_order_status: status of ONE order, for a verified asker only (data map 4.5). Reads vw_chat_orders and
vw_chat_order_items only.

Who may look up an order:
  - logged in:  ctx.customer_uuid (from your session) must own the order. The model cannot set it.
  - guest:      order number AND the billing email must both match. Rate limited per visitor.
Every failure to find an order (no such order, wrong email, someone else's order) returns the SAME answer.

Returned on purpose: order number, order status, date placed, grand total, item names and quantities.
Never returned: names, emails, phones, addresses, payment data, tracking claims.
payment_status is not selected at all: finding 9 (order_status and payment_status disagree) is still open, so the
bot only says what the order is marked as. When the store decides which field is the truth, add it here.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

ALLOWED_KEYS = {"order_number", "email"}
MAX_ITEMS = 25
RATE_MAX_LOOKUPS = 5          # different (order, email) guesses ...
RATE_WINDOW_SECONDS = 15 * 60  # ... per visitor per window. The same guess repeated is not a new guess.

_ORDER_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_]{0,29}")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}")

NO_MATCH = {
    "found": False,
    "note": ("No order matches those details. Begin your reply by saying you couldn't find an order matching those details, "
             "then ask them to double-check the order number and the billing email. "
             "Do not say whether the order number exists or which detail was wrong. "
             "Tell the customer to check the order number and the billing email, or contact the store "
             "(use get_store_info for the phone or email). Never guess or try other numbers."),
}
RATE_LIMITED = {
    "error": "rate_limited",
    "note": ("Too many order lookups from this visitor. Do not try again. Tell the customer to wait a while "
             "or contact the store (use get_store_info)."),
}
NOT_AVAILABLE = {
    "error": "verification_unavailable",
    "note": "Order lookup is not available right now. Tell the customer to contact the store (use get_store_info).",
}
STATUS_NOTE = ("Begin your reply with the order's status, for example \"Order 1234 is marked Processing\", using the order_status and order number in this result, and list the items if the customer asked about them. Never reply with only the store's phone and email. Say only that the order is marked with this status. Do not say whether payment was received, and do "
               "not promise shipping, delivery dates, tracking or cancellation: none of that is on file. "
               "You cannot cancel, change, refund or return an order. If the customer asked for any of that, say plainly that it "
               "cannot be done in this chat, then give the store's phone and email so they can ask the store (use get_store_info). "
               "Never repeat the customer's email, and never work out totals yourself.")


class AttemptLimiter:
    """Sliding window of distinct guesses per visitor, in memory (one process).
    With several workers or servers use a shared store (Redis) instead: same idea, same numbers."""

    def __init__(self, max_attempts: int = RATE_MAX_LOOKUPS, window_seconds: float = RATE_WINDOW_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.max_attempts, self.window, self.clock = max_attempts, window_seconds, clock
        self._hits: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str, fingerprint: str) -> bool:
        now = self.clock()
        with self._lock:
            if len(self._hits) > 10_000:                      # keep memory bounded
                self._hits = {c: h for c, h in ((c, self._fresh(h, now)) for c, h in self._hits.items()) if h}
            hits = self._fresh(self._hits.get(client_id, {}), now)
            if fingerprint in hits:
                self._hits[client_id] = hits
                return True
            if len(hits) >= self.max_attempts:
                self._hits[client_id] = hits
                return False
            hits[fingerprint] = now
            self._hits[client_id] = hits
            return True

    def _fresh(self, hits: dict[str, float], now: float) -> dict[str, float]:
        return {fp: t for fp, t in hits.items() if now - t < self.window}

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = AttemptLimiter()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def _qty(value: Any) -> Any:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def get_order_status(args: dict[str, Any], ctx=None, run_query: Callable | None = None,
                     limiter: AttemptLimiter | None = None) -> dict[str, Any]:
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}

    number = args.get("order_number")
    if not isinstance(number, str) or not number.strip():
        return {"error": "order_number is required: ask the customer for their order number"}
    number = number.strip().lstrip("#").strip()
    customer_uuid = getattr(ctx, "customer_uuid", None)
    client_id = getattr(ctx, "client_id", None)

    if customer_uuid:                                          # logged in: ownership comes from the session
        who_sql, who_value, guess = "customer_uuid = %s", customer_uuid, None
    else:                                                      # guest: order number + billing email
        email = args.get("email")
        if not isinstance(email, str) or not email.strip():
            return {"error": "email_required",
                    "note": "Ask the customer for the billing email used on the order. It is needed to look it up."}
        email = email.strip().lower()
        if len(email) > 254 or not _EMAIL.fullmatch(email):
            return {"error": "invalid_email", "note": "That does not look like an email address. Ask the customer to check it."}
        if not client_id:                                      # fail closed: no visitor id, no rate limit, no lookup
            log.error("get_order_status called without ctx.client_id")
            return dict(NOT_AVAILABLE)
        guess = _hash(f"{number}|{email}")
        if not (limiter or LIMITER).allow(client_id, guess):
            log.warning("order lookup rate limited client=%s", _hash(client_id)[:10])
            return dict(RATE_LIMITED)
        who_sql, who_value = "billing_email_hash = %s", _hash(email)

    if not _ORDER_NUMBER.fullmatch(number):
        return dict(NO_MATCH)                                  # same answer as "not found"; no query needed

    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    rows = run_query(
        f"SELECT order_uuid, order_number, order_status, grand_total, created_at FROM vw_chat_orders "
        f"WHERE order_number = %s AND {who_sql} LIMIT 1", [number, who_value])
    log.info("order lookup mode=%s email=%s matched=%s", "session" if customer_uuid else "guest",
             (guess or "-")[:10], bool(rows))                  # hashed, never the email, never the result
    if not rows:
        return dict(NO_MATCH)

    order = rows[0]
    items = run_query(
        "SELECT product_name, product_sku, product_qty FROM vw_chat_order_items WHERE order_uuid = %s LIMIT %s",
        [order["order_uuid"], MAX_ITEMS])
    placed = order.get("created_at")
    return {
        "found": True,
        "order_number": order["order_number"],
        "order_status": order.get("order_status") or "not on file",
        "placed_on": str(placed)[:10] if placed else None,
        "grand_total": order.get("grand_total"),
        "items": [{"name": i["product_name"], "sku": i["product_sku"], "quantity": _qty(i["product_qty"])} for i in items],
        "note": STATUS_NOTE,
    }
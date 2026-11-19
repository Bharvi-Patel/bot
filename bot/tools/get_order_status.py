"""get_order_status: the status of the customer's OWN orders, for a logged-in customer only.

The chat server puts the customer's login token in ctx.customer_token (from the real session, never from the model or the
message). The tool sends it to the store API's own customer endpoint (GET /orders), and the API returns only that
customer's orders, so ownership is decided by the API as well as checked here. No database is touched.

A visitor without a login token gets a "please log in" answer. There is no guest lookup by order number and email.
Every miss (no such order on this account) gets the SAME answer, so nothing reveals whether an order number exists.

Returned on purpose: order number, order status, date placed, grand total, item names. Never returned: names, emails, phones,
addresses, payment data, tracking claims, quantities (the list endpoint does not carry them).
payment_status is not used: order_status and payment_status can disagree (finding 9), so the bot only says what the order is
marked as. When the store decides which field is the truth, add it here.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from bot.api_client import ApiError, get_client

log = logging.getLogger(__name__)

ALLOWED_KEYS = {"order_number"}
MAX_ORDERS = 25               # how many of the customer's most recent orders are read
LIST_LIMIT = 5                # how many are shown when the customer gives no order number
MAX_ITEMS = 25

_ORDER_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_]{0,29}")

NO_MATCH = {
    "found": False,
    "note": ("No order on this account matches that order number. Begin your reply by saying you couldn't find an order matching "
             "those details, then ask them to double-check the order number or contact the store "
             "(use get_store_info for the phone or email). Do not say whether the order number exists. "
             "Never guess or try other numbers."),
}
NO_ORDERS = {
    "found": False,
    "note": ("There are no orders on this account. Say so plainly, and suggest contacting the store if they expected one "
             "(use get_store_info for the phone or email)."),
}
LOGIN_REQUIRED = {
    "error": "login_required",
    "note": ("Order questions are answered only for a logged-in customer, and this visitor is not logged in. Tell them to log in "
             "to their account and ask again, or to contact the store (use get_store_info for the phone or email). "
             "Do not ask for an order number, email or any personal details in this chat."),
}
LOGIN_EXPIRED = {
    "error": "login_expired",
    "note": "The customer's login is no longer valid. Tell them to log in again and ask again, or to contact the store (use get_store_info).",
}
NOT_AVAILABLE = {
    "error": "orders_unavailable",
    "note": "Order lookup is not available right now. Tell the customer to contact the store (use get_store_info).",
}
STATUS_NOTE = ("Begin your reply with the order's status, for example \"Order 1234 is marked Processing\", using the order_status "
               "and order number in this result, and list the items if the customer asked. Say only that the order is marked with that status. "
               "Do not say whether payment was received or promise shipping, delivery dates, tracking or cancellation: none of that "
               "is on file. Do not say whether payment was received. "
               "You cannot cancel, change, refund or return an order. If the customer asked for any of that, say plainly that it "
               "cannot be done in this chat, then give the store's phone and email so they can ask the store (use get_store_info). "
               "Never reply with only the store's phone and email: answer the order question first. "
               "Never repeat the customer's email, and never work out totals yourself.")


def _shape(order: dict[str, Any]) -> dict[str, Any]:
    placed = order.get("created_at")
    return {
        "order_number": order["order_number"],
        "order_status": order.get("order_status") or "not on file",
        "placed_on": str(placed)[:10] if placed else None,
        "grand_total": order.get("grand_total"),
        "items": [{"name": i["name"], "sku": i.get("sku")} for i in (order.get("items") or [])[:MAX_ITEMS]],
    }


def get_order_status(args: dict[str, Any], ctx=None, client=None) -> dict[str, Any]:
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}

    token = getattr(ctx, "customer_token", None)
    if not token:                                              # a visitor: no lookup at all, and nothing to ask for
        return dict(LOGIN_REQUIRED)

    number = args.get("order_number")
    wanted = None
    if number is not None:
        if not isinstance(number, str) or not number.strip():
            return {"error": "order_number must be text; leave it out to see the recent orders"}
        wanted = number.strip().lstrip("#").strip()
        if not _ORDER_NUMBER.fullmatch(wanted):
            return dict(NO_MATCH)                              # same answer as "not found"; no request needed

    try:
        orders = (client or get_client(ctx)).customer_orders(token, limit=MAX_ORDERS)
    except ApiError as exc:
        if exc.status in (401, 403):
            return dict(LOGIN_EXPIRED)
        log.warning("order lookup failed: status=%s", exc.status)          # the status only: no message, no token
        return dict(NOT_AVAILABLE)

    if wanted:
        match = next((o for o in orders if o["order_number"].lower() == wanted.lower()), None)
        log.info("order lookup by number matched=%s", bool(match))        # never the number or the result
        return {"found": True, **_shape(match), "note": STATUS_NOTE} if match else dict(NO_MATCH)
    if not orders:
        return dict(NO_ORDERS)
    return {"found": True, "orders": [_shape(o) for o in orders[:LIST_LIMIT]], "note": STATUS_NOTE}

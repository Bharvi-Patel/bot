"""get_shipping_options: what the store's own shipping text says about shipping and pickup.

There is no public API endpoint for shipping zones and prices yet (the existing routes are admin only, and the bot
holds no admin access and no database access). Until one exists, this tool answers from the store's shipping,
delivery and pickup wording in its policy pages and FAQs (the store-scoped index behind search_policies).
It therefore has NO exact prices of its own: a price or a shipping area may only be repeated if the page text
states it. The catalog has no shipment or tracking data either, so it cannot give delivery dates.
"""
from __future__ import annotations

from typing import Any, Callable

ALLOWED_KEYS = {"zone_keyword"}
SOURCE_TYPES = ["policy", "guide", "faq"]
BASE_QUESTION = "shipping delivery cost where we ship pickup"

NOTE = (
    "This is the store's own policy text, not a rate table. Answer ONLY from it and name the page it came from. "
    "Give a shipping price, a shipping area or a pickup option only if the text states it. If it does not, say you "
    "don't have exact shipping prices for that and suggest contacting the store (use get_store_info). "
    "Never promise a delivery date: none is on file. If the customer asks how long delivery takes or when something "
    "will arrive, begin your reply with \"I don't have delivery time information\" (do not say the store does not "
    "provide delivery dates), then share what the text does say and suggest contacting the store."
)
NOT_FOUND = {
    "found": False,
    "note": ("No shipping information was found in the store's pages. Do not guess prices, shipping areas or pickup. "
             "Say you don't have shipping details and give the store's phone or email (use get_store_info)."),
}
UNAVAILABLE = {
    "error": "shipping_information_unavailable",
    "note": "Do not guess. Point the customer to the store phone or email (get_store_info).",
}


def get_shipping_options(args: dict[str, Any], ctx=None, search: Callable | None = None) -> dict[str, Any]:
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}
    keyword = args.get("zone_keyword")
    if keyword is not None and (not isinstance(keyword, str) or len(keyword) > 100):
        return {"error": "zone_keyword must be short text"}

    if search is None:
        from bot.tools.search_policies import search_policies as search
    question = f"{BASE_QUESTION} {keyword.strip()}" if keyword and keyword.strip() else BASE_QUESTION
    result = search({"question": question, "source_types": SOURCE_TYPES}, ctx=ctx)
    if not isinstance(result, dict) or "error" in result:
        return dict(UNAVAILABLE)
    if not result.get("found"):
        return dict(NOT_FOUND)
    return {"found": True, "source": "store_policy_text", "chunks": result.get("chunks", []), "note": NOTE}

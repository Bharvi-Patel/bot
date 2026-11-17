"""list_brands: the brands the store carries. Reads the store API (GET /brands), never the database."""
from __future__ import annotations

from typing import Any

from bot.api_client import ApiClient, ApiError, get_client


def list_brands(args: dict[str, Any], client: ApiClient | None = None, ctx=None) -> dict[str, Any]:
    if args:
        return {"error": "this tool takes no parameters"}
    try:
        rows = (client or get_client(ctx)).brands()
    except ApiError:
        return {"error": "The brand list is not available right now. Do not guess brands; offer the store's contact details."}

    brands = []
    for r in rows:
        # status 0 is skipped. That it means "switched off" is not confirmed with the API team yet;
        # every brand in the saved real response has status 1, so this changes nothing today.
        if r.get("status") == 0 or not r.get("slug") or not r.get("name"):
            continue
        brands.append({"slug": r["slug"], "name": r["name"]})
    brands.sort(key=lambda b: b["name"].lower())
    if not brands:
        return {"found": False, "note": "No brand information is on file."}
    return {
        "found": True,
        "count": len(brands),
        "brands": brands,
        "note": (
            "Name every brand listed above, all of them; do not shorten or summarize the list. "
            "This list does not say how many products each brand has or whether any are in stock; "
            "do not claim a brand has products available. To see a brand's products, search with its slug."
        ),
    }

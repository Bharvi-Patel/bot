"""list_attribute_values: the values one attribute group (Color, Bed Size, Material ...) has among the products that
match a keyword. Reads the store API (the facets of POST /products/search), never the database.

Lets the model turn "queen" into the exact value the catalog uses before it calls search_products. The API has no
store-wide list of values, so a keyword (the product type, e.g. "bed") is required, and only values that exist on
products matching it are returned. The API gives no product counts, so none are shown.
"""
from __future__ import annotations

from typing import Any

from bot.api_client import ApiClient, ApiError, get_client
from bot.tools.search_products import ATTRIBUTE_GROUPS

MAX_VALUES = 40
ALLOWED_KEYS = {"group", "keyword"}


def list_attribute_values(args: dict[str, Any], client: ApiClient | None = None, ctx=None) -> dict[str, Any]:
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}
    group = args.get("group")
    if not isinstance(group, str) or not group.strip():
        return {"error": f"group is required; one of: {', '.join(ATTRIBUTE_GROUPS)}"}
    match = next((g for g in ATTRIBUTE_GROUPS if g.lower() == group.strip().lower()), None)
    if match is None:
        return {"error": f"unknown group. Use one of: {', '.join(ATTRIBUTE_GROUPS)}"}
    keyword = args.get("keyword")
    if not isinstance(keyword, str) or len(keyword.strip()) < 2:
        return {"error": "keyword_required",
                "note": "Give a keyword of at least 2 letters for the product type (for example 'bed' or 'rug'); the values "
                        "are listed for the products that match it."}
    keyword = keyword.strip()[:100]

    try:
        options = (client or get_client(ctx)).attribute_options(keyword)
    except ApiError:
        return {"error": "The attribute list is not available right now. Do not guess values; offer the store's contact details."}

    values: list[str] = []
    for g in options:
        if isinstance(g.get("group"), str) and g["group"].lower() == match.lower():
            values = [v["name"] for v in g.get("values") or [] if v.get("name")]
            break
    if not values:
        return {"group": match, "keyword": keyword, "found": False,
                "note": (f"No product matching '{keyword}' has {match} information in the store's data, so search_products cannot "
                         f"filter by {match} for it. Search without that filter, tell the customer the results are not "
                         f"filtered by {match}, and never say a product matches unless its name says so.")}
    values = sorted(dict.fromkeys(values), key=str.lower)[:MAX_VALUES]
    return {
        "group": match,
        "keyword": keyword,
        "found": True,
        "values": [{"value": v} for v in values],
        "note": ("These are the values found on products matching the keyword, not the whole store. Pass a value exactly as "
                 "written to search_products (attribute_group + attribute_value) together with the same keyword. A search "
                 "filtered this way lists only products that carry the value in the store's data; a product can fit and "
                 "not appear because only its name says so, so never call the results every product of that value."),
    }

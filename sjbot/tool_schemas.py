"""What the model is told about each tool. Names and parameters must match the tools' own allowed keys
(tests/test_router.py checks this), because the tools reject anything they don't know."""
from __future__ import annotations

from sjbot.tools.search_products import ATTRIBUTE_GROUPS, SORTS

_STR = {"type": "string"}
_NUM = {"type": "number"}


def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    decl: dict = {"name": name, "description": description}
    if properties:
        decl["parameters"] = {"type": "object", "properties": properties}
        if required:
            decl["parameters"]["required"] = required
    return decl


TOOL_DECLARATIONS: list[dict] = [
    _tool(
        "search_products",
        "Filter the catalog by price, category, brand, color, material, size or keyword. Returns real products with live "
        "prices. Use for 'show me ...' and 'cheapest ...' questions. Get category_slug from list_categories first. "
        "Shows at most 8 results sorted by name unless you set sort: for cheapest use sort=price_asc, for most expensive price_desc.",
        {
            "keyword": {**_STR, "description": "Words from the product name, e.g. 'loveseat'."},
            "category_slug": {**_STR, "description": "Slug returned by list_categories."},
            "brand_slug": {**_STR, "description": "Slug returned by list_brands."},
            "min_price": _NUM,
            "max_price": _NUM,
            "color": _STR,
            "material": _STR,
            "attribute_group": {**_STR, "enum": list(ATTRIBUTE_GROUPS),
                                "description": "Give together with attribute_value."},
            "attribute_value": {**_STR, "description": "Exact value from list_attribute_values."},
            "max_width_in": {**_NUM, "description": "Widest the product may be, in inches."},
            "sort": {**_STR, "enum": list(SORTS)},
        },
    ),
    _tool(
        "get_product_details",
        "Everything known about ONE product: live price, description, attributes, dimensions, images. Give a sku "
        "(preferred) or a slug taken from another tool's result.",
        {"sku": _STR, "slug": _STR},
    ),
    _tool(
        "recommend_products",
        "Semantic product finder for open-ended needs ('a sofa for a small apartment'). Prices in the result are live. "
        "Pass the customer's budget as max_price when they give one.",
        {
            "query": {**_STR, "description": "What the customer wants, in plain words."},
            "max_price": _NUM,
            "min_price": _NUM,
            "brand_slug": _STR,
            "category_slug": _STR,
            "limit": {"type": "integer", "description": "1 to 8, default 5."},
        },
        ["query"],
    ),
    _tool(
        "search_policies",
        "Store policy, care instructions, buying guides, FAQs and brand information (returns, warranty, financing, "
        "delivery policy, cleaning, mattress guides). Answer only from what it returns.",
        {
            "question": {**_STR, "description": "The customer's question in plain words."},
            "source_types": {"type": "array", "items": {**_STR, "enum": ["policy", "guide", "faq", "brand_info", "blog"]},
                             "description": "Optional. Defaults to policy, guide and faq."},
        },
        ["question"],
    ),
    _tool(
        "list_categories",
        "Browse the category tree or find a category by name. Returns the slugs search_products needs. "
        "Use either parent_slug or keyword, not both.",
        {"parent_slug": _STR, "keyword": _STR},
    ),
    _tool("list_brands", "The brands the store carries, with their slugs. Takes no parameters."),
    _tool(
        "list_attribute_values",
        "The values that exist for one attribute group (for example the exact color or bed size names), so you can "
        "turn 'queen' into the catalog's own value before calling search_products.",
        {"group": {**_STR, "enum": list(ATTRIBUTE_GROUPS)}},
        ["group"],
    ),
    _tool(
        "get_shipping_options",
        "Shipping methods and flat prices per zone, including in-store pickup. Use for ANY question about shipping cost "
        "or where the store ships; call it with no parameters to see every zone. The listed zones are the only places the store ships to. "
        "Cannot give delivery dates.",
        {"zone_keyword": {**_STR, "description": "Optional filter on a zone or method name, e.g. 'USA' or 'pickup'. Leave it out to see everything."}},
    ),
    _tool("get_store_info", "Store phone, email, address and opening hours. Takes no parameters."),
    _tool(
        "check_inventory",
        "Stock check for a SKU. Stock data is not available, so it always answers 'unknown'.",
        {"sku": _STR},
        ["sku"],
    ),
    _tool(
        "get_order_status",
        "Status of ONE order. Needs the order number and the billing email the customer used on the order (ask for "
        "whichever is missing; never guess). Returns the order's status, date, total and item names, nothing else. "
        "Cannot give payment confirmation, tracking or delivery dates, and cannot cancel or change orders.",
        {
            "order_number": {**_STR, "description": "The order number the customer gave you."},
            "email": {**_STR, "description": "The billing email the customer typed. Omit it if they have not given one."},
        },
        ["order_number"],
    ),
]
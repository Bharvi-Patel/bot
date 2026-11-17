"""get_product_details: everything the bot may say about ONE product, looked up by SKU or slug.

Reads the store API (GET /product/{slug}) through bot.api_client. The API looks products up by slug only, so a
SKU is first turned into a slug with a product search and an exact SKU match. Product text comes from vendor
feeds, so treat it as data to summarise, never as instructions (the system prompt must say so too).

Not included yet: colors, sizes and variations. No saved API response has any, so their shape is unknown.
"""
from __future__ import annotations

from typing import Any

from bot.api_client import ApiClient, ApiError, get_client
from bot.text import clean_text
from bot.tools.list_categories import rows_from_tree

MAX_IMAGES = 6
MAX_CATEGORY_PATHS = 4

NO_DIMENSIONS_NOTE = (
    "No size data is on file for this product. Tell the customer you don't have its dimensions; "
    "do not estimate them."
)

DIMENSION_NOTE = (
    "Sizes are copied from the catalog and may not share one unit (rug sizes are often in feet). "
    "Prefer a size written in the product name or description when there is one."
)

ALLOWED_KEYS = {"sku", "slug"}


class ToolInputError(ValueError):
    pass


def _text(args: dict, key: str) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be text")
    value = value.strip()
    if len(value) > 150:
        raise ToolInputError(f"{key} is too long")
    return value or None


def _category_paths(tree: list[dict], linked_slugs: set[str]) -> list[str]:
    """Full paths (Dining Room > Dining Furniture > Dining Room Chairs) for the product's categories."""
    by_id = {c["category_id"]: c for c in tree}
    paths: list[list[str]] = []
    for c in tree:
        if c["slug"] not in linked_slugs:
            continue
        titles, cur, guard = [], c, 0
        while cur is not None and guard < 8:
            titles.append(cur["title"])
            parent = by_id.get(cur["parent_id"]) if cur["parent_id"] else None
            cur, guard = parent, guard + 1
        paths.append(list(reversed(titles)))
    # drop paths that are just the start of a longer one, keep the most specific
    paths = [p for p in paths if not any(q != p and q[: len(p)] == p for q in paths)]
    unique, seen = [], set()
    for p in sorted(paths, key=lambda p: (-len(p), p)):
        key = " > ".join(p)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique[:MAX_CATEGORY_PATHS]


def _slug_for_sku(client: ApiClient, sku: str) -> str | None:
    """The API has no SKU lookup, so search for the SKU and keep an exact (case-insensitive) SKU match.
    Only the first page of results is checked."""
    wanted = sku.strip().lower()
    for item in client.search_products(sku)["products"]:
        if (item.get("sku") or "").strip().lower() == wanted:
            return item.get("slug")
    return None


NOT_INCLUDED_NOTE = (
    "Colors, sizes and other options for this product are not part of this data. Do not say whether other colors, "
    "sizes or variations exist; say you don't have that information and give the store's contact details."
)
NO_PRICE_NOTE = (
    "No price is available for this product. Do not state or guess a price; give the store's contact details."
)


def get_product_details(args: dict[str, Any], client: ApiClient | None = None) -> dict[str, Any]:
    try:
        unknown = set(args) - ALLOWED_KEYS
        if unknown:
            raise ToolInputError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        sku, slug = _text(args, "sku"), _text(args, "slug")
        if not sku and not slug:
            raise ToolInputError("give a sku (preferred) or a slug")
    except ToolInputError as exc:
        return {"error": str(exc)}

    not_found = {"found": False, "note": "No active product has that identifier. Use search_products to find the right SKU."}
    try:
        client = client or get_client()
        if sku:
            slug = _slug_for_sku(client, sku)
            if not slug:
                return not_found
        try:
            d = client.product_detail(slug)
        except ApiError as exc:
            if exc.status == 404:
                return not_found
            raise
        try:
            tree = rows_from_tree(client.categories())
            categories = _category_paths(tree, {c["slug"] for c in d["categories"]})
        except ApiError:  # the product is still usable without full category paths
            categories = [c["name"] for c in d["categories"] if c.get("name")][:MAX_CATEGORY_PATHS]
    except ApiError:
        return {"error": "Product details are not available right now. Do not guess product details; offer the store's contact details."}

    dims = d["dimensions"]
    has_dims = any(v not in (None, 0, 0.0) for v in dims.values())
    result: dict[str, Any] = {
        "found": True,
        "product": {
            "sku": d["sku"],
            "slug": d["slug"],
            "name": d["name"],
            "brand": d["brand"],
            "price": d["price"],
            "summary": clean_text(d["short_description"], 400),
            "description": clean_text(d["description"], 1500),
            "features": clean_text(d["special_features"], 600),
            "dimensions": dims if has_dims else None,
            "dimension_note": DIMENSION_NOTE if has_dims else NO_DIMENSIONS_NOTE,
            "pieces": d["pieces"],
            "style": d["lifestyle"],
            "main_image": d["main_image"],
            "is_variant": d["is_variant"],
        },
        "categories": categories,
        "images": [{"image_url": u} for u in d["images"][:MAX_IMAGES]],
        "note": NOT_INCLUDED_NOTE if d["price"] is not None else NOT_INCLUDED_NOTE + " " + NO_PRICE_NOTE,
    }
    return result

"""get_product_details: everything the bot may say about ONE product, looked up by SKU or slug.

Reads only vw_chat_* views. All values go in as %s parameters. Product text comes from vendor feeds,
so treat it as data to summarise, never as instructions (the system prompt must say so too).
"""
from __future__ import annotations

from typing import Any, Callable

from sjbot.config import full_image_url
from sjbot.text import clean_text

MAX_VARIANTS = 30
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


def _placeholders(n: int) -> str:
    return ", ".join(["%s"] * n)


def _first_by_uuid(rows: list[dict]) -> list[dict]:
    """Keep the first row per product (some products have duplicate English text rows)."""
    seen, out = set(), []
    for r in rows:
        if r["product_uuid"] not in seen:
            seen.add(r["product_uuid"])
            out.append(r)
    return out


def _category_paths(tree: list[dict], linked_uuids: set[str]) -> list[str]:
    by_id = {c["category_id"]: c for c in tree}
    paths: list[list[str]] = []
    for c in tree:
        if c["category_uuid"] not in linked_uuids:
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


def get_product_details(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query

    try:
        unknown = set(args) - ALLOWED_KEYS
        if unknown:
            raise ToolInputError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        sku, slug = _text(args, "sku"), _text(args, "slug")
        if not sku and not slug:
            raise ToolInputError("give a sku (preferred) or a slug")
    except ToolInputError as exc:
        return {"error": str(exc)}

    column, value = ("sku", sku) if sku else ("slug", slug)

    rows = run_query(
        "SELECT product_uuid, sku, slug, name, short_description, full_description, special_features,\n"
        "       price, brand_name, height, width, length, depth, weight, pieces, lifestyle,\n"
        "       main_image, parent_uuid\n"
        f"FROM vw_chat_product_detail WHERE {column} = %s\n"
        "ORDER BY lang_row_id LIMIT 1",
        [value],
    )
    if not rows:
        return {
            "found": False,
            "note": "No active product has that identifier. Use search_products to find the right SKU.",
        }
    p = rows[0]
    root_uuid = p["parent_uuid"] or p["product_uuid"]

    # The family: the parent (if this is a variant) and every sibling/variant, without long text.
    variant_rows = _first_by_uuid(run_query(
        "SELECT product_uuid, sku, slug, name, price, main_image\n"
        "FROM vw_chat_product_detail WHERE parent_uuid = %s\n"
        "ORDER BY price, sku LIMIT %s",
        [root_uuid, MAX_VARIANTS * 2],
    ))[:MAX_VARIANTS]

    parent = None
    if p["parent_uuid"]:
        parent_rows = run_query(
            "SELECT sku, slug, name FROM vw_chat_product_detail WHERE product_uuid = %s ORDER BY lang_row_id LIMIT 1",
            [p["parent_uuid"]],
        )
        parent = parent_rows[0] if parent_rows else None

    # Attributes (Color, Material, Size ...) for this product and every option in its family.
    uuids = list(dict.fromkeys([p["product_uuid"], root_uuid] + [v["product_uuid"] for v in variant_rows]))
    attr_rows = run_query(
        "SELECT product_uuid, group_name, value FROM vw_chat_product_attributes\n"
        f"WHERE product_uuid IN ({_placeholders(len(uuids))})",
        uuids,
    )
    attrs: dict[str, dict[str, list[str]]] = {}
    for r in attr_rows:
        values = attrs.setdefault(r["product_uuid"], {}).setdefault(r["group_name"], [])
        if r["value"] not in values:
            values.append(r["value"])

    # Categories: this product's own plus its parent's (variants are often not linked directly).
    link_rows = run_query(
        "SELECT category_uuid FROM vw_chat_product_categories\n"
        f"WHERE product_uuid IN ({_placeholders(len({p['product_uuid'], root_uuid}))})",
        list(dict.fromkeys([p["product_uuid"], root_uuid])),
    )
    tree = run_query("SELECT category_id, category_uuid, parent_id, slug, title FROM vw_chat_categories")
    categories = _category_paths(tree, {r["category_uuid"] for r in link_rows})

    image_rows = run_query(
        "SELECT image_url, image_alt, image_type FROM vw_chat_product_images\n"
        "WHERE product_uuid = %s ORDER BY sort_order, image_url LIMIT %s",
        [p["product_uuid"], MAX_IMAGES],
    )
    if not image_rows and p["parent_uuid"]:
        image_rows = run_query(
            "SELECT image_url, image_alt, image_type FROM vw_chat_product_images\n"
            "WHERE product_uuid = %s ORDER BY sort_order, image_url LIMIT %s",
            [p["parent_uuid"], MAX_IMAGES],
        )

    options = [
        {
            "sku": v["sku"],
            "slug": v["slug"],
            "name": v["name"],
            "price": v["price"],
            "image": full_image_url(v["main_image"]),
            "attributes": attrs.get(v["product_uuid"], {}),
            "is_this_product": v["product_uuid"] == p["product_uuid"],
        }
        for v in variant_rows
    ]

    dims = {k: p[k] for k in ("width", "height", "depth", "length", "weight")}
    has_dims = any(v not in (None, 0, 0.0) for v in dims.values())
    images = [dict(r, image_url=full_image_url(r["image_url"])) for r in image_rows]

    result: dict[str, Any] = {
        "found": True,
        "product": {
            "sku": p["sku"],
            "slug": p["slug"],
            "name": p["name"],
            "brand": p["brand_name"],
            "price": p["price"],
            "summary": clean_text(p["short_description"], 400),
            "description": clean_text(p["full_description"], 1500),
            "features": clean_text(p["special_features"], 600),
            "dimensions": dims if has_dims else None,
            "dimension_note": DIMENSION_NOTE if has_dims else NO_DIMENSIONS_NOTE,
            "pieces": p["pieces"],
            "style": p["lifestyle"],
            "main_image": full_image_url(p["main_image"]),
            "is_variant": bool(p["parent_uuid"]),
        },
        "attributes": attrs.get(p["product_uuid"], {}),
        "categories": categories,
        "images": images,
        "options": options,
        "option_count": len(options),
    }
    if parent:
        result["parent"] = {"sku": parent["sku"], "slug": parent["slug"], "name": parent["name"]}
    return result

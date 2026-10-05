"""search_products: filter the catalog with a fixed, parameterized query on the vw_chat_* views.

The model only chooses filter values. It never writes SQL. Everything user-controlled goes into
`params`; the only text built into the SQL comes from constants in this file.

Parents and variants are both searched (colors and sizes often live on variant rows), then results
are collapsed to ONE row per product family: the cheapest matching option, plus how many options match.
"""
from __future__ import annotations

from typing import Any, Callable

from sjbot.config import full_image_url

MAX_RESULTS = 8  # hard cap, not a parameter

# Sort choices map to fixed SQL fragments. Unknown values are rejected, never pasted in.
# These run on the collapsed result, so they use plain column names (no table alias).
SORTS = {
    "name": "name ASC",
    "price_asc": "price ASC, name ASC",
    "price_desc": "price DESC, name ASC",
}

# One product family = the parent and all its variants. A product with no parent is its own family.
FAMILY = "COALESCE(NULLIF(p.parent_uuid, ''), p.product_uuid)"

ALLOWED_KEYS = {
    "keyword", "category_slug", "brand_slug", "min_price", "max_price",
    "color", "material", "attribute_group", "attribute_value", "max_width_in", "sort",
}

# Attribute groups that exist in the catalog (SELECT DISTINCT group_name FROM vw_chat_product_attributes).
ATTRIBUTE_GROUPS = [
    "Bar Features", "Bed Size", "Color", "Frame Size", "Item Type", "Leaf Type", "Material",
    "Pattern", "Rug Size", "Shape", "Table Base", "Table Shape", "Table Top",
]
COLOR_GROUP = "Color"
MATERIAL_GROUP = "Material"


class ToolInputError(ValueError):
    """The model sent parameters this tool won't accept."""


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _number(args: dict, key: str) -> float | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"{key} must be a number")
    if value < 0:
        raise ToolInputError(f"{key} cannot be negative")
    return float(value)


def _text(args: dict, key: str, max_len: int = 100) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be text")
    value = value.strip()
    if len(value) > max_len:
        raise ToolInputError(f"{key} is too long")
    return value or None


def _prepare(args: dict[str, Any]) -> dict[str, Any]:
    """Validate everything the model sent and return clean values."""
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        raise ToolInputError(f"unknown parameter(s): {', '.join(sorted(unknown))}")

    vals: dict[str, Any] = {
        "keyword": _text(args, "keyword"),
        "category_slug": _text(args, "category_slug"),
        "brand_slug": _text(args, "brand_slug"),
        "color": _text(args, "color"),
        "material": _text(args, "material"),
        "min_price": _number(args, "min_price"),
        "max_price": _number(args, "max_price"),
        "max_width": _number(args, "max_width_in"),
        "sort": args.get("sort") or "name",
    }

    group, value = _text(args, "attribute_group"), _text(args, "attribute_value")
    if bool(group) != bool(value):
        raise ToolInputError("attribute_group and attribute_value must be given together")
    if group:
        match = next((g for g in ATTRIBUTE_GROUPS if g.lower() == group.lower()), None)
        if match is None:
            raise ToolInputError(f"attribute_group must be one of: {', '.join(ATTRIBUTE_GROUPS)}")
        group = match
    vals["attribute_group"], vals["attribute_value"] = group, value

    if vals["sort"] not in SORTS:
        raise ToolInputError(f"sort must be one of: {', '.join(SORTS)}")
    lo, hi = vals["min_price"], vals["max_price"]
    if lo is not None and hi is not None and lo > hi:
        raise ToolInputError("min_price is greater than max_price")
    return vals


def _clauses(v: dict[str, Any], width_mode: str) -> tuple[list[str], list[Any], list[str], list[Any]]:
    """Build (ctes, cte_params, where, params).

    width_mode: "filter"  -> only products with a real width up to max_width_in
                "missing" -> only products with no width on file (used to tell the model what was skipped)
                "none"    -> ignore width
    """
    ctes: list[str] = []
    cte_params: list[Any] = []
    where: list[str] = []
    params: list[Any] = []

    if v["category_slug"]:
        # The category plus everything below it. parent_id is the parent's integer category_id.
        ctes.append(
            "sub AS (\n"
            "  SELECT category_id, category_uuid FROM vw_chat_categories WHERE slug = %s\n"
            "  UNION ALL\n"
            "  SELECT c.category_id, c.category_uuid FROM vw_chat_categories c\n"
            "  JOIN sub ON c.parent_id = sub.category_id\n"
            ")"
        )
        cte_params.append(v["category_slug"])
        # Computed once (not per product row), so the search stays fast.
        ctes.append(
            "cat_products AS (\n"
            "  SELECT DISTINCT pc.product_uuid FROM vw_chat_product_categories pc\n"
            "  JOIN sub ON sub.category_uuid = pc.category_uuid\n"
            ")"
        )
        # A row matches if it, or its parent, is in the category (variants may not be linked directly).
        where.append(
            "(p.product_uuid IN (SELECT product_uuid FROM cat_products) "
            "OR p.parent_uuid IN (SELECT product_uuid FROM cat_products))"
        )

    if v["keyword"]:
        for word in v["keyword"].split()[:5]:
            where.append("CONCAT_WS(' ', p.name, par.name) LIKE %s")
            params.append(f"%{_like_escape(word)}%")
    if v["brand_slug"]:
        where.append("COALESCE(p.brand_slug, par.brand_slug) = %s")
        params.append(v["brand_slug"])
    if v["min_price"] is not None:
        where.append("p.price >= %s")
        params.append(v["min_price"])
    if v["max_price"] is not None:
        where.append("p.price <= %s")
        params.append(v["max_price"])

    if v["max_width"] is not None:
        if width_mode == "filter":
            where.append("p.width > 0 AND p.width <= %s")
            params.append(v["max_width"])
        elif width_mode == "missing":
            where.append("(p.width IS NULL OR p.width = 0)")

    attr_sql = (
        "p.product_uuid IN (SELECT a.product_uuid FROM vw_chat_product_attributes a "
        "WHERE a.group_name = %s AND a.value = %s)"
    )
    if v["color"]:
        where.append(attr_sql)
        params.extend([COLOR_GROUP, v["color"]])
    if v["material"]:
        where.append(
            "p.product_uuid IN (SELECT a.product_uuid FROM vw_chat_product_attributes a "
            "WHERE a.group_name = %s AND a.value LIKE %s)"
        )
        params.extend([MATERIAL_GROUP, f"%{_like_escape(v['material'])}%"])
    if v["attribute_group"]:
        where.append(attr_sql)
        params.extend([v["attribute_group"], v["attribute_value"]])

    # The products table is used twice (the row and its parent); a CTE builds it once.
    ctes.append("base AS (SELECT * FROM vw_chat_product_search)")
    return ctes, cte_params, where, params


def _with(ctes: list[str]) -> str:
    return "WITH RECURSIVE " + ",\n".join(ctes) + "\n"


def build_search_query(args: dict[str, Any]) -> tuple[str, list[Any]]:
    """Validate args and return (sql, params). Pure function: no database needed, easy to unit test."""
    v = _prepare(args)
    ctes, cte_params, where, params = _clauses(v, "filter" if v["max_width"] is not None else "none")
    sql = (
        _with(ctes)
        + "SELECT sku, slug, name, price, brand_name, width, height, depth, main_image, matching_options\n"
        + "FROM (\n"
        + "  SELECT p.sku, p.slug, p.name, p.price,\n"
        + "         COALESCE(p.brand_name, par.brand_name) AS brand_name,\n"
        + "         p.width, p.height, p.depth,\n"
        + "         COALESCE(p.main_image, par.main_image) AS main_image,\n"
        + f"         COUNT(*) OVER (PARTITION BY {FAMILY}) AS matching_options,\n"
        + f"         ROW_NUMBER() OVER (PARTITION BY {FAMILY} ORDER BY p.price ASC, p.sku ASC) AS rn\n"
        + "  FROM base p\n"
        + "  LEFT JOIN base par ON par.product_uuid = p.parent_uuid\n"
        + ("  WHERE " + "\n    AND ".join(where) + "\n" if where else "")
        + ") x\n"
        + "WHERE rn = 1\n"
        + f"ORDER BY {SORTS[v['sort']]}\n"
        + f"LIMIT {MAX_RESULTS}"
    )
    return sql, cte_params + params


def build_missing_size_query(args: dict[str, Any]) -> tuple[str, list[Any]] | None:
    """Count families that match every filter except size AND have no width on file.
    None when no width filter was used."""
    v = _prepare(args)
    if v["max_width"] is None:
        return None
    ctes, cte_params, where, params = _clauses(v, "missing")
    sql = (
        _with(ctes)
        + f"SELECT COUNT(DISTINCT {FAMILY}) AS n\n"
        + "FROM base p\n"
        + "LEFT JOIN base par ON par.product_uuid = p.parent_uuid\n"
        + "WHERE " + "\n  AND ".join(where)
    )
    return sql, cte_params + params


def search_products(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    """Tool entry point. Returns a JSON-friendly dict for the model."""
    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    try:
        sql, params = build_search_query(args)
        missing = build_missing_size_query(args)
    except ToolInputError as exc:
        return {"error": str(exc)}

    rows = run_query(sql, params)
    for row in rows:
        row["main_image"] = full_image_url(row.get("main_image"))
    result: dict[str, Any] = {"count": len(rows), "products": rows}
    if len(rows) == MAX_RESULTS:
        result["note"] = f"Showing the first {MAX_RESULTS} matches; there may be more. Suggest narrowing the filters."
    if not rows:
        result["note"] = "No products matched. Suggest loosening a filter (price, size or color)."
        if args.get("max_width_in") is not None:
            result["note"] = ("No product with a width on file fits that size limit. Do NOT say the catalog has no such products: "
                              "say that none of the ones with a listed width fit, mention any without size data (see size_note), "
                              "and suggest a larger limit or confirming sizes with the store.")

    if missing is not None:
        n = int(run_query(*missing)[0]["n"])
        result["without_size_data"] = n
        if n:
            result["size_note"] = (
                f"{n} other matching product(s) have no width on file, so they could not be checked "
                "against the size limit. Say so, and suggest confirming the size with the store."
            )
    return result
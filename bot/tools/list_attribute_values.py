"""list_attribute_values: the values that exist for one attribute group (Color, Bed Size, Material ...).

Lets the model turn "queen" into the exact value the catalog uses before it calls search_products.
"""
from __future__ import annotations

from typing import Any, Callable

from sjbot.tools.search_products import ATTRIBUTE_GROUPS

MAX_VALUES = 40


def list_attribute_values(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    unknown = set(args) - {"group"}
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}
    group = args.get("group")
    if not isinstance(group, str) or not group.strip():
        return {"error": f"group is required; one of: {', '.join(ATTRIBUTE_GROUPS)}"}
    match = next((g for g in ATTRIBUTE_GROUPS if g.lower() == group.strip().lower()), None)
    if match is None:
        return {"error": f"unknown group. Use one of: {', '.join(ATTRIBUTE_GROUPS)}"}

    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    rows = run_query(
        "SELECT value, COUNT(DISTINCT product_uuid) AS products\n"
        "FROM vw_chat_product_attributes WHERE group_name = %s\n"
        "GROUP BY value ORDER BY products DESC, value LIMIT %s",
        [match, MAX_VALUES],
    )
    return {
        "group": match,
        "values": [{"value": r["value"], "products": int(r["products"])} for r in rows],
        "note": "Pass a value exactly as written here to search_products (attribute_group + attribute_value).",
    }

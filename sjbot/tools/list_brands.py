"""list_brands: the brands the store carries. Reads vw_chat_brands only."""
from __future__ import annotations

from typing import Any, Callable


def list_brands(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    if args:
        return {"error": "this tool takes no parameters"}
    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    rows = run_query("SELECT brand_slug, brand_name, active_products FROM vw_chat_brands ORDER BY brand_name")

    seen, brands = set(), []
    for r in rows:
        key = (r["brand_slug"] or r["brand_name"] or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        brands.append({
            "slug": r["brand_slug"],
            "name": r["brand_name"],
            "products_available": int(r["active_products"] or 0),
        })
    if not brands:
        return {"found": False, "note": "No brand information is on file."}
    return {
        "found": True,
        "count": len(brands),
        "brands": brands,
        "note": (
            "Present brands with products_available above 0 as brands the store carries now. "
            "A brand with 0 has no current products; do not list it as available."
        ),
    }

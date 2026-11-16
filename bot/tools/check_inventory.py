"""check_inventory: STUB. The catalog has no stock data (finding 1), so this always says unknown.

When a real stock source exists, replace the body and keep the same return shape.
"""
from __future__ import annotations

from typing import Any


def check_inventory(args: dict[str, Any], run_query=None) -> dict[str, Any]:
    unknown = set(args) - {"sku"}
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}
    sku = args.get("sku")
    if not isinstance(sku, str) or not sku.strip() or len(sku) > 150:
        return {"error": "sku is required"}
    return {
        "sku": sku.strip(),
        "availability": "unknown",
        "message": (
            "Stock levels are not available to the assistant. Tell the customer availability is unknown "
            "and ask them to contact the store to confirm (use get_store_info for the phone number). "
            "Never say an item is in stock, out of stock, or give a delivery date."
        ),
    }

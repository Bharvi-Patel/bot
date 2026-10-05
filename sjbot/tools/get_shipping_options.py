"""get_shipping_options: enabled shipping methods per zone with flat prices. Reads vw_chat_shipping only.

The catalog has no shipment or tracking data, so this tool cannot give delivery dates.
"""
from __future__ import annotations

import re
from typing import Any, Callable

ALLOWED_KEYS = {"zone_keyword"}


def _norm(text: Any) -> str:
    """Lowercase letters and digits only, so 'pickup', 'Pick Up' and 'In-Store Pickup' all compare equal."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _price(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return str(raw)  # e.g. a text rule the store typed in


def get_shipping_options(args: dict[str, Any], run_query: Callable | None = None) -> dict[str, Any]:
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        return {"error": f"unknown parameter(s): {', '.join(sorted(unknown))}"}
    keyword = args.get("zone_keyword")
    if keyword is not None and (not isinstance(keyword, str) or len(keyword) > 100):
        return {"error": "zone_keyword must be short text"}

    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    rows = run_query("SELECT zone_name, method_name, flat_price, note FROM vw_chat_shipping ORDER BY zone_name, method_name")
    needle = _norm(keyword)
    if needle:                                       # matches the zone name OR the method name
        rows = [r for r in rows if needle in _norm(r["zone_name"]) or needle in _norm(r["method_name"])]

    zones: dict[str, list[dict]] = {}
    for r in rows:
        zones.setdefault(r["zone_name"], []).append({
            "method": r["method_name"],
            "price": _price(r["flat_price"]),
            "note": r["note"] or None,
        })
    if not zones:
        return {"found": False, "note": "No zone or method matches that word. Call again with no parameters to see every option before answering; if still unsure, suggest contacting the store."}
    return {
        "found": True,
        "zones": [{"zone": z, "methods": m} for z, m in zones.items()],
        "note": (
            "Prices are the store's flat rates only; weight-based or free-shipping rules may exist that "
            "are not shown here. Never promise a delivery date: none is on file."
        ),
    }
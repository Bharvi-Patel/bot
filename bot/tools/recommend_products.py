"""recommend_products: semantic product finder = "RAG finds it, SQL confirms it" (data map 5.4, 5.5).

1. Hybrid search over the product documents in rag_chunks (vector + Postgres full-text, merged), with the hard filters
   the model extracted (max/min price, brand, category) applied in the query as metadata filters.
2. Every candidate is then re-read from live SQL with get_product_details. The name and price the bot may quote come ONLY
   from that live read. Products that no longer exist are dropped, and the price filters are checked again on the live price.
Reads rag_chunks (Postgres, RAG_DB_URL) and, through get_product_details, the read-only vw_chat_* views.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from sjbot.tools.search_policies import _default_run_query, _vector_literal, fuse, keyword_query

log = logging.getLogger(__name__)

CUTOFF = float(os.environ.get("SJ_RAG_PRODUCT_CUTOFF", "0.55"))     # tune on the evaluation set
FETCH = 12           # candidates per search
CANDIDATES = 10      # after merging, how many get the live SQL check
DEFAULT_LIMIT, MAX_LIMIT = 5, 8
ALLOWED = {"query", "max_price", "min_price", "brand_slug", "category_slug", "limit"}

COLS = "id, source_key, title, url_path, content, brand_slug, price, 1 - (embedding <=> %s::vector) AS similarity"
BASE = "FROM rag_chunks WHERE source_type = 'product' AND embedding IS NOT NULL"

NOTE = ("Names and prices here come from live SQL: quote only these, never a price from anywhere else. Recommend only the "
        "products listed. If fewer than the customer wanted are listed, say so. No medical claims. Do not claim stock or "
        "delivery dates (use check_inventory / get_shipping_options).")
NOTE_NONE = ("No matching products were found. Say so plainly and offer to search another category or budget. "
             "Do not invent products or prices.")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _filters(a: dict) -> tuple[str, list]:
    sql, params = "", []
    if a.get("max_price") is not None: sql += " AND price <= %s"; params.append(a["max_price"])
    if a.get("min_price") is not None: sql += " AND price >= %s"; params.append(a["min_price"])
    if a.get("brand_slug"): sql += " AND brand_slug = %s"; params.append(a["brand_slug"])
    if a.get("category_slug"): sql += " AND %s = ANY(category_slugs)"; params.append(a["category_slug"])
    return sql, params


def _sku(content: str) -> str | None:
    m = re.search(r"SKU:\s*(.+)$", content, re.M)
    return m.group(1).strip() if m else None


def recommend_products(args: dict[str, Any], run_query: Callable | None = None, embed_query: Callable | None = None,
                       confirm: Callable | None = None) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) - ALLOWED:
        return {"error": f"allowed parameters: {sorted(ALLOWED)}"}
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"error": "query is required: describe what the customer wants"}
    query = query.strip()[:300]
    for k in ("max_price", "min_price"):
        if args.get(k) is not None and not _num(args[k]):
            return {"error": f"{k} must be a number"}
    for k in ("brand_slug", "category_slug"):
        if args.get(k) is not None and not isinstance(args[k], str):
            return {"error": f"{k} must be text"}
    limit = args.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        return {"error": f"limit must be a whole number from 1 to {MAX_LIMIT}"}

    try:
        if embed_query is None:
            from sjbot.embeddings import embed_query as default_embed
            embed_query = default_embed
        if confirm is None:
            from sjbot.tools.get_product_details import get_product_details as confirm
        run_query = run_query or _default_run_query
        vec = _vector_literal(embed_query(query))
        fsql, fparams = _filters(args)
        vector_rows = run_query(f"SELECT {COLS} {BASE}{fsql} ORDER BY embedding <=> %s::vector LIMIT %s", [vec, *fparams, vec, FETCH])
        kw = keyword_query(query)
        keyword_rows = run_query(
            f"SELECT {COLS} {BASE}{fsql} AND to_tsvector('english', content) @@ websearch_to_tsquery('english', %s) "
            "ORDER BY ts_rank_cd(to_tsvector('english', content), websearch_to_tsquery('english', %s)) DESC LIMIT %s",
            [vec, *fparams, kw, kw, FETCH]) if kw else []
    except Exception:
        log.exception("recommend_products search failed")
        return {"error": "product search is unavailable right now", "note": "Do not guess products. Point the customer to the store (get_store_info)."}

    best = max((float(r["similarity"]) for r in vector_rows), default=None)
    candidates = fuse(vector_rows, keyword_rows, CUTOFF, CANDIDATES)

    products, dropped = [], 0
    for r in candidates:                               # SQL confirms it
        sku = _sku(r["content"])
        try:
            live = confirm({"sku": sku}) if sku else {"found": False}
        except Exception:
            log.exception("live check failed for %s", sku)
            live = {"found": False}
        prod = live.get("product") if live.get("found") else None
        price = prod.get("price") if prod else None
        if prod is None or price is None:
            dropped += 1; continue
        if (args.get("max_price") is not None and price > args["max_price"]) or (args.get("min_price") is not None and price < args["min_price"]):
            dropped += 1; continue
        products.append({"sku": sku, "name": prod.get("name") or r["title"], "price": price, "page": r["url_path"],
                         "brand_slug": r["brand_slug"], "similarity": round(float(r["similarity"]), 3),
                         "live": prod, "attributes": live.get("attributes", {})})
        if len(products) == limit:
            break

    if not products:
        return {"found": False, "cutoff": CUTOFF, "best_similarity": round(best, 3) if best is not None else None,
                "dropped_by_live_check": dropped, "note": NOTE_NONE}
    return {"found": True, "count": len(products), "dropped_by_live_check": dropped, "products": products, "note": NOTE}

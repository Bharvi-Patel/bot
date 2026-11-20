"""Read-only diagnostic probe for the remaining Phase 1 API integration gaps.

Never modifies any data, never touches MySQL or Postgres, and redacts PII/secrets before printing.

Usage:
    python dev_tools/probe_api_gaps.py <store_uuid>
    python dev_tools/probe_api_gaps.py <store_uuid> --customer-token <bearer_token>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.api_client import ApiClient, ApiConfig, ApiError
from rag.api_products import scope_from_tree

PII_KEYS = {
    "name", "first_name", "last_name", "full_name", "customer_name", "email", "billing_email",
    "shipping_email", "phone", "mobile", "address", "address1", "address2", "street", "city",
    "state", "zipcode", "zip", "postal_code", "ip", "ip_address", "token", "access_token",
    "password", "secret", "turnstile_secret_key", "stripe_secret", "base_price", "cost",
}


def redact_shape(obj: Any, depth: int = 0) -> Any:
    """Return the JSON key/type structure with sample safe fields and all PII/secrets scrubbed."""
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in PII_KEYS or any(w in kl for w in ("secret", "password", "token", "email", "phone", "address", "card")):
                out[k] = f"<{type(v).__name__}:redacted>"
            else:
                out[k] = redact_shape(v, depth + 1)
        return out
    if isinstance(obj, list):
        if not obj:
            return []
        return [redact_shape(obj[0], depth + 1), f"... ({len(obj)} total)"] if len(obj) > 1 else [redact_shape(obj[0], depth + 1)]
    if isinstance(obj, str):
        return obj[:60] + ("..." if len(obj) > 60 else "")
    return obj


def probe_mattress_gap(client: ApiClient) -> None:
    print("\n=== 1. MATTRESS GAP & CATEGORY PAGING ===")
    cat = "mattresses-and-bedding"
    tests = [
        ("unfiltered {}", {}),
        ("price_from=0 (current)", {"price_range": {"from": 0}}),
        ("price_from=-1", {"price_range": {"from": -1}}),
        ("per_page=16", {"per_page": 16}),
        ("per_page=40", {"per_page": 40}),
        ("sort_by=az", {"sort_by": "az"}),
    ]
    for label, body in tests:
        try:
            p1 = client._request("POST", f"/categories/{cat}/products", body=body)
            d1 = (p1.get("data") or {}).get("product") or {}
            items1 = d1.get("data") or []
            p2 = client._request("POST", f"/categories/{cat}/products", params={"page": 2}, body=body)
            d2 = (p2.get("data") or {}).get("product") or {}
            items2 = d2.get("data") or []
            s1 = items1[0].get("slug") if items1 else None
            s2 = items2[0].get("slug") if items2 else None
            same = "SAME PAGE 1 (cached bug!)" if (s1 and s1 == s2) else "distinct page 2 (paging works!)"
            neg = sum(1 for x in items1 if str(x.get("sale_price", "")).startswith("-"))
            pt1 = sum(1 for x in items1 if x.get("product_type") == 1)
            print(f"  {label:24s} -> total={d1.get('total')}, last_page={d1.get('last_page')}, "
                  f"p1_type1={pt1}, p1_neg_price={neg} | p2: {same}")
        except Exception as exc:
            print(f"  {label:24s} -> ERROR: {exc}")

    # Also check subcategories under mattresses-and-bedding
    tree = client.categories()
    _, missing, scope = scope_from_tree(tree, [cat], [])
    print(f"  Subcategories in scope ({len(scope)}, missing={missing}):")
    for slug in scope:
        try:
            r_raw = client._request("POST", f"/categories/{slug}/products", body={})
            t_raw = ((r_raw.get("data") or {}).get("product") or {}).get("total")
            r_cur = client._request("POST", f"/categories/{slug}/products", body={"price_range": {"from": 0}})
            t_cur = ((r_cur.get("data") or {}).get("product") or {}).get("total")
            diff = "  <-- DROPPED BY price_from=0!" if t_raw != t_cur else ""
            print(f"    {slug}: raw_total={t_raw}, price_from_0_total={t_cur}{diff}")
        except Exception as exc:
            print(f"    {slug}: ERROR {exc}")

    # Quick brand search check for previously-missing mattress brands
    for q in ("beautyrest", "nectar", "sierra sleep"):
        try:
            res = client.search_products(q)
            print(f"  search_products({q!r}) -> total={res.get('total')}, p1_count={len(res.get('products') or [])}")
        except Exception as exc:
            print(f"  search_products({q!r}) -> ERROR {exc}")


def probe_blogs(client: ApiClient) -> None:
    print("\n=== 2. BLOG PAGINATION ===")
    for label, params in [
        ("default", None),
        ("page=2", {"page": 2}),
        ("page=2&limit=10", {"page": 2, "limit": 10}),
        ("limit=50", {"limit": 50}),
        ("per_page=50", {"per_page": 50}),
    ]:
        try:
            p = client._request("GET", "/blogs/active", params=params)
            rows = p.get("blogs") or []
            first = rows[0].get("slug") if rows and isinstance(rows[0], dict) else None
            print(f"  {label:16s} -> totalRecord={p.get('totalRecord')}, currentPage={p.get('currentPage')}, "
                  f"limit={p.get('limit')}, got={len(rows)}, first_slug={first}")
        except Exception as exc:
            print(f"  {label:16s} -> ERROR: {exc}")


def probe_pages_and_products(client: ApiClient) -> None:
    print("\n=== 3. ADVANCED PAGES (is_advanced) ===")
    pages = client.store_pages()
    advanced = []
    for pg in pages:
        slug = pg.get("slug")
        if not slug:
            continue
        try:
            raw = client._request("GET", f"/page/{slug}")
            row = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
            langs = row.get("page_languages") if isinstance(row, dict) else []
            lang0 = langs[0] if isinstance(langs, list) and langs else {}
            if lang0.get("is_advanced"):
                advanced.append((slug, row))
        except Exception:
            continue
    print(f"  Found {len(advanced)} advanced page(s) out of {len(pages)} total pages.")
    if advanced:
        slug, row = advanced[0]
        print(f"  Sample advanced page slug={slug!r} shape:")
        print(json.dumps(redact_shape(row), indent=2))

    print("\n=== 4. PRODUCT DETAIL ATTRIBUTES / COLORS / SIZES / PROMOTIONS ===")
    found = {"attributes": None, "colors": None, "sizes": None, "promotions": None}
    try:
        res = client.search_products("bed")
        slugs = [p["slug"] for p in (res.get("products") or [])[:10] if p.get("slug")]
    except Exception:
        slugs = []
    for slug in slugs:
        try:
            raw = client._request("GET", f"/product/{slug}")
            data = raw.get("data") or {}
            prod = data.get("product") or {}
            for k in ("colors", "sizes"):
                if not found[k] and data.get(k):
                    found[k] = (slug, redact_shape(data[k]))
            for k in ("attributes", "promotions"):
                if not found[k] and prod.get(k):
                    found[k] = (slug, redact_shape(prod[k]))
        except Exception:
            continue
    for k, val in found.items():
        if val:
            print(f"  Non-empty {k} on slug={val[0]!r}: {json.dumps(val[1])}")
        else:
            print(f"  No non-empty {k} found in first {len(slugs)} 'bed' products.")


def probe_orders(client: ApiClient, token: str) -> None:
    print("\n=== 5. REAL /orders & /orders/{id} STRUCTURE (REDACTED) ===")
    try:
        raw = client._request("GET", "/orders", params={"page": 1, "limit": 5},
                              extra_headers={"Authorization": f"Bearer {token}"})
        print("  GET /orders shape:")
        print(json.dumps(redact_shape(raw), indent=2))
        orders = raw.get("orders") if isinstance(raw, dict) else None
        if isinstance(orders, list) and orders and isinstance(orders[0], dict):
            oid = orders[0].get("uuid") or orders[0].get("id")
            if oid:
                detail = client._request("GET", f"/orders/{oid}",
                                         extra_headers={"Authorization": f"Bearer {token}"})
                print(f"  GET /orders/{oid} shape:")
                print(json.dumps(redact_shape(detail), indent=2))
    except ApiError as exc:
        print(f"  GET /orders ERROR (status={exc.status}): {exc}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Read-only diagnostic for Phase 1 API gaps.")
    ap.add_argument("store_uuid")
    ap.add_argument("--customer-token", default="", help="Optional logged-in customer Bearer token to inspect /orders shape (PII is redacted).")
    args = ap.parse_args(argv)
    client = ApiClient(ApiConfig.from_env(args.store_uuid))
    probe_mattress_gap(client)
    probe_blogs(client)
    probe_pages_and_products(client)
    if args.customer_token:
        probe_orders(client, args.customer_token.strip())
    else:
        print("\n=== 5. /orders SKIPPED (pass --customer-token <Bearer token> from browser DevTools to inspect) ===")


if __name__ == "__main__":
    main()

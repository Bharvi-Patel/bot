"""Second capture: the questions the first run left open. READ-ONLY, same setup as capture_filters.py.

In PowerShell, in the bot repo:
    $env:BOT_API_BASE_URL = "http://127.0.0.1:8001/"
    $env:BOT_STORE_UUID = "<your store uuid>"
    python capture_filters2.py

It writes ./captured2/ (one file per call + summary.txt). Send me the folder or summary.txt.

What it answers:
  A. Does attribute_ids work on a category listing, using an attribute that belongs to that category?
     (tested on both "Beds" categories in the tree, bedroom-furniture-beds and kids-furniture-beds)
  B. What does /product-filter-data return for those categories, now that they have attribute groups?
  C. Do several filters work together in one request (brand + price + sort, attribute + price)?
  D. Do products priced -0.01 get past a price filter, and do "from" only / "to" only work?
  E. Do page and per_page work with a filter on, so the bot can read past the first 16?
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BOT_API_BASE_URL"].rstrip("/")
UUID = os.environ["BOT_STORE_UUID"]
OUT = Path("captured2")
OUT.mkdir(exist_ok=True)
summary = []


def call(label, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Store-UUID": UUID, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        status, raw = None, f"{type(e).__name__}: {e}"
    (OUT / f"{label}.json").write_text(
        json.dumps({"request": {"method": method, "path": path, "body": body}, "status": status, "response": raw},
                   indent=1), encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    extra = ""
    prod = ((parsed or {}).get("data") or {}).get("product") if isinstance(parsed, dict) else None
    if isinstance(prod, dict):
        rows = prod.get("data") or []
        prices = [p.get("sale_price") for p in rows]
        real = [x for x in prices if isinstance(x, (int, float)) and x > 0]
        extra = (f"total={prod.get('total')} page={prod.get('current_page')}/{prod.get('last_page')} "
                 f"per_page={prod.get('per_page')} on_page={len(rows)} "
                 f"price_min={min(real) if real else None} price_max={max(real) if real else None} "
                 f"nonpositive_prices={len(prices) - len(real)}")
    line = f"{label:38} {method} {path:42} status={status} {extra}"
    summary.append(line)
    print(line)
    return parsed


def walk(nodes):
    for n in nodes or []:
        yield n
        yield from walk(n.get("children"))


# Queen's id and one brand id come from a real search response, not from memory
base = call("00_search_bed", "POST", "/products/search", {"q": "bed"})
facets = ((base or {}).get("data") or {}).get("filter") or {}
queen = None
for g in facets.get("attributes") or []:
    for a in g.get("attributes") or []:
        if g.get("group_name") == "Bed Size" and a.get("name") == "Queen":
            queen = a["uuid"]
brand = next((b["uuid"] for b in facets.get("brands") or [] if b.get("uuid")), None)
summary.append(f"test values: queen={queen} brand={brand}")

tree = call("01_categories_tree", "GET", "/categories/tree")
slugs = [n["slug"] for n in walk(tree if isinstance(tree, list) else []) if n.get("slug") in
         ("bedroom-furniture-beds", "kids-furniture-beds")]
summary.append(f"category slugs used: {slugs}")

for slug in slugs:
    p = f"/categories/{slug}/products"
    s = slug.split("-")[0]
    call(f"10_{s}_unfiltered", "POST", p, {})
    if queen:
        call(f"11_{s}_queen", "POST", p, {"attribute_ids": [queen]})
        call(f"12_{s}_queen_price_sort", "POST", p, {"attribute_ids": [queen], "price_range": {"from": 100, "to": 1500},
                                                   "sort_by": "price_low_high"})
    if brand:
        call(f"13_{s}_brand_price_sort", "POST", p, {"brand_ids": [brand], "price_range": {"from": 100, "to": 1500},
                                                   "sort_by": "price_low_high"})
    call(f"14_{s}_price_from_only", "POST", p, {"price_range": {"from": 1000}})
    call(f"15_{s}_price_to_only", "POST", p, {"price_range": {"to": 300}})
    call(f"16_{s}_price_100_1500", "POST", p, {"price_range": {"from": 100, "to": 1500}})
    call(f"17_{s}_page2", "POST", p + "?page=2", {})
    call(f"18_{s}_per_page_40_body", "POST", p, {"per_page": 40})
    call(f"19_{s}_per_page_40_query", "POST", p + "?per_page=40", {})
    call(f"20_{s}_filter_data", "POST", "/product-filter-data", {"category_slug": slug})

# the same checks on keyword search
call("30_search_queen_price_sort", "POST", "/products/search",
     {"q": "bed", "attribute_ids": [queen] if queen else [], "price_range": {"from": 100, "to": 1500},
      "sort_by": "price_low_high"})
call("31_search_price_100_1500", "POST", "/products/search", {"q": "bed", "price_range": {"from": 100, "to": 1500}})
call("32_search_page2_brand", "POST", "/products/search?page=2", {"q": "bed", "brand_ids": [brand]} if brand else {"q": "bed"})
call("33_search_per_page_40", "POST", "/products/search", {"q": "bed", "per_page": 40})

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
print("\nSaved to ./captured2/ ; send the folder or summary.txt")
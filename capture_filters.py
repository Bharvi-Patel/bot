"""Capture real storefront API responses for the filter formats the bot does not know yet.

Run it from the bot repo on the machine where the API runs (port 8001):

    set BOT_API_BASE_URL=http://127.0.0.1:8001/      (use the same base URL the bot's other calls use)
    set BOT_STORE_UUID=<your store uuid>
    python capture_filters.py

It only READS: search calls and filter-data calls. Nothing is created or changed.
It writes one file per attempt into ./captured/ (request sent, HTTP status, raw response) and prints a
summary. Parameter names (brand_ids, price_range {from,to}, attribute_ids, sort_by) come from the
tool-to-API map. Which body shape the API accepts is NOT known, so every variant is tried and the
total count is compared with the unfiltered one: a filter that works changes the total.
Then send me the ./captured/ folder (or its summary.txt).
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BOT_API_BASE_URL"].rstrip("/")
UUID = os.environ["BOT_STORE_UUID"]
OUT = Path("captured")
OUT.mkdir(exist_ok=True)
summary = []


def call(label, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Store-UUID": UUID, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, raw = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # could not connect at all
        status, raw = None, f"{type(e).__name__}: {e}"
    (OUT / f"{label}.json").write_text(
        json.dumps({"request": {"method": method, "path": path, "body": body}, "status": status, "response": raw},
                   indent=1), encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    total = count = None
    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        prod = parsed["data"].get("product")
        if isinstance(prod, dict):
            total, count = prod.get("total"), len(prod.get("data") or [])
    line = f"{label:34} {method} {path:34} status={status} total={total} on_page={count}"
    summary.append(line)
    print(line)
    return parsed


# 1. unfiltered search: gives real brand / attribute ids and the price range to test with
base = call("00_search_unfiltered", "POST", "/products/search", {"q": "bed"})
facets = ((base or {}).get("data") or {}).get("filter") or {}
brand = next((b for b in facets.get("brands") or [] if b.get("uuid")), None)
group = next((g for g in facets.get("attributes") or [] if g.get("attributes")), None)
attr = group["attributes"][0] if group else None
price = facets.get("price_range") or {}
lo, hi = price.get("min"), price.get("max")
mid = round((lo + hi) / 2, 2) if lo is not None and hi is not None else 500
summary.append(f"test values: brand={brand} attribute_group={group and group.get('group_name')} attribute={attr} price={price}")

# 2. each filter on its own, in the shapes the map suggests
variants = []
if brand:
    variants += [
        ("brand_ids_list", {"q": "bed", "brand_ids": [brand["uuid"]]}),
        ("brand_ids_string", {"q": "bed", "brand_ids": brand["uuid"]}),
    ]
if attr:
    variants += [
        ("attribute_ids_list", {"q": "bed", "attribute_ids": [attr["uuid"]]}),
        ("attribute_ids_string", {"q": "bed", "attribute_ids": attr["uuid"]}),
    ]
variants += [
    ("price_from_to", {"q": "bed", "price_range": {"from": lo, "to": mid}}),
    ("price_min_max", {"q": "bed", "price_range": {"min": lo, "max": mid}}),
]
for s in ("price_low_high", "price_high_low", "az", "za"):
    variants.append((f"sort_{s}", {"q": "bed", "sort_by": s}))
for label, body in variants:
    call("10_search_" + label, "POST", "/products/search", body)

# 3. the same filters on a category listing (needs a real category slug)
tree = call("20_categories_tree", "GET", "/categories/tree")
slug = None
nodes = tree if isinstance(tree, list) else (tree or {}).get("data") if isinstance(tree, dict) else None
for n in nodes or []:
    if isinstance(n, dict) and n.get("slug"):
        slug = n["slug"]
        break
if slug:
    call("21_category_unfiltered", "POST", f"/categories/{slug}/products", {})
    for label, body in variants:
        body = {k: v for k, v in body.items() if k != "q"}
        call("22_category_" + label, "POST", f"/categories/{slug}/products", body)
else:
    summary.append("no category slug found in /categories/tree; category tests skipped")

# 4. the endpoint the map names as the likely source of attribute values
call("30_filter_data_empty", "POST", "/product-filter-data", {})
call("31_filter_data_q", "POST", "/product-filter-data", {"q": "bed"})
if slug:
    call("32_filter_data_category", "POST", "/product-filter-data", {"category_slug": slug})
call("33_filter_data_get", "GET", "/product-filter-data")

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
print("\nSaved to ./captured/ ; compare total= on each filtered line with the unfiltered one (line 00).")
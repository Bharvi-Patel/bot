"""Third capture, READ-ONLY, same setup as before. Two open questions:

  1. Paging on a category listing. In the last run POST /categories/{slug}/products?page=2 came back as page 1,
     while POST /products/search?page=2 correctly came back as page 2. This tries other ways to ask for page 2
     and prints the first product's slug for each call, so you can see whether the page really changed.
  2. Where attribute values (Queen, Black ...) can be listed for the whole store. /product-filter-data returned
     no attribute groups, so this checks what POST /products/search returns for an empty or missing query.

In PowerShell, in the bot repo:
    $env:BOT_API_BASE_URL = "http://127.0.0.1:8001/"
    $env:BOT_STORE_UUID = "<your store uuid>"
    python capture_filters3.py
Send ./captured3/summary.txt (or the folder).
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BOT_API_BASE_URL"].rstrip("/")
UUID = os.environ["BOT_STORE_UUID"]
OUT = Path("captured3")
OUT.mkdir(exist_ok=True)
summary = []
SLUG = "kids-furniture-beds"  # 84 products, 6 pages in the last run


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
    data_block = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    prod = data_block.get("product") if isinstance(data_block, dict) else None
    if isinstance(prod, dict):
        rows = prod.get("data") or []
        first = (rows[0].get("slug") or "")[:40] if rows else None
        extra = f"total={prod.get('total')} page={prod.get('current_page')}/{prod.get('last_page')} first={first}"
    if isinstance(data_block, dict) and isinstance(data_block.get("filter"), dict):
        f = data_block["filter"]
        extra += (f" | facets: brands={len(f.get('brands') or [])} groups="
                  f"{[g.get('group_name') for g in f.get('attributes') or []]}")
    line = f"{label:34} {method} {path:50} status={status} {extra}"
    summary.append(line)
    print(line)
    return parsed


p = f"/categories/{SLUG}/products"
call("10_page1", "POST", p, {})
call("11_query_page2", "POST", p + "?page=2", {})
call("12_query_page3", "POST", p + "?page=3", {})
call("13_body_page2", "POST", p, {"page": 2})
call("14_body_page3", "POST", p, {"page": 3})
call("15_query_and_body_page2", "POST", p + "?page=2", {"page": 2})
call("16_query_page2_nobody", "POST", p + "?page=2")
call("17_brand_query_page2", "POST", p + "?page=2", {"price_range": {"from": 100, "to": 1500}})

# whole-store facets
call("20_search_empty_q", "POST", "/products/search", {"q": ""})
call("21_search_no_q", "POST", "/products/search", {})
call("22_search_q_a", "POST", "/products/search", {"q": "a"})
call("23_search_q_black", "POST", "/products/search", {"q": "black"})

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
print("\nSaved to ./captured3/ ; compare first= across the page lines.")
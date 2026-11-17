"""Fourth capture, READ-ONLY. Run in PowerShell from the bot repo, after setting the same two variables:
    $env:BOT_API_BASE_URL = "http://127.0.0.1:8001/"
    $env:BOT_STORE_UUID = "<your store uuid>"
    python capture_filters4.py
Send ./captured4/summary.txt.

Questions, from reading the API's code (not yet tested):
  1. Does price_range {"from": 0} leave the results unchanged (same total as no filter) while making
     category paging work (page 2 and 3 differ from page 1)?
  2. Does per_page work on a category listing once a filter is present?
  3. When two attribute ids from different groups are sent together, does the API return products that have
     BOTH, or EITHER? (Compare the three totals: Queen alone, Black alone, Queen + Black.)
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ["BOT_API_BASE_URL"].rstrip("/")
UUID = os.environ["BOT_STORE_UUID"]
OUT = Path("captured4")
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
    block = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    prod = block.get("product") if isinstance(block, dict) else None
    if isinstance(prod, dict):
        rows = prod.get("data") or []
        first = (rows[0].get("slug") or "")[:36] if rows else None
        extra = (f"total={prod.get('total')} page={prod.get('current_page')}/{prod.get('last_page')} "
                 f"per_page={prod.get('per_page')} on_page={len(rows)} first={first}")
    line = f"{label:32} {method} {path:48} status={status} {extra}"
    summary.append(line)
    print(line)
    return parsed


def find_attr(facets, group, name):
    for g in (facets or {}).get("attributes") or []:
        if (g.get("group_name") or "").lower() == group.lower():
            for a in g.get("attributes") or []:
                if (a.get("name") or "").lower() == name.lower():
                    return a.get("uuid")
    return None


# real ids, taken from search facets
r1 = call("00_search_bed", "POST", "/products/search", {"q": "bed"})
r2 = call("01_search_black", "POST", "/products/search", {"q": "black"})
queen = find_attr(((r1 or {}).get("data") or {}).get("filter"), "Bed Size", "Queen")
black = find_attr(((r2 or {}).get("data") or {}).get("filter"), "Color", "Black")
summary.append(f"test values: queen={queen} black={black}")

# 3. attribute combination on search
if queen and black:
    call("10_bed_queen", "POST", "/products/search", {"q": "bed", "attribute_ids": [queen]})
    call("11_bed_black", "POST", "/products/search", {"q": "bed", "attribute_ids": [black]})
    call("12_bed_queen_black", "POST", "/products/search", {"q": "bed", "attribute_ids": [queen, black]})
else:
    summary.append("could not find both Queen (Bed Size) and Black (Color) in the facets; attribute test skipped")

# 1 and 2. no-op filter for category paging, per_page
for slug in ("kids-furniture-beds", "bedroom-furniture-beds"):
    s = slug.split("-")[0]
    p = f"/categories/{slug}/products"
    nf = {"price_range": {"from": 0}}
    call(f"20_{s}_nofilter", "POST", p, {})
    call(f"21_{s}_from0_p1", "POST", p, nf)
    call(f"22_{s}_from0_p2", "POST", p + "?page=2", nf)
    call(f"23_{s}_from0_p3", "POST", p + "?page=3", nf)
    call(f"24_{s}_from0_per40", "POST", p, {**nf, "per_page": 40})
    call(f"25_{s}_from0_per40_p2", "POST", p + "?page=2", {**nf, "per_page": 40})

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
print("\nSaved to ./captured4/")
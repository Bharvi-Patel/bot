"""Run a few real searches against your dev database as chatbot_ro.
Set the SJ_DB_* variables first (see .env.example), then:  python smoke_search.py
"""
import json
import time

from sjbot.tools.search_products import search_products

CASES = [
    {"category_slug": "sofas-and-seating-sofas", "max_price": 1000},
    {"color": "Black", "min_price": 300, "max_price": 1200, "max_width_in": 80},
    {"keyword": "sectional", "sort": "price_asc"},
    {"brand_slug": "signature-design-by-ashley", "max_price": 500},
]

for case in CASES:
    print("\n==", json.dumps(case))
    t0 = time.time()
    result = search_products(case)
    print("time: %.2fs" % (time.time() - t0))
    print("count:", result.get("count"), result.get("note", result.get("error", "")))
    for p in result.get("products", [])[:3]:
        print("  ", p["sku"], "|", p["name"], "|", p["price"], "|", p["brand_name"], "| options:", p["matching_options"])

"""Why do retest-typo-dresser, retest-parts-bed and retest-refine-dining fail?

For each case this prints (1) the rows the GRADER expects (its truth SQL on vw_chat_products) with the categories they are
filed under, and (2) what search_products actually returns for the searches the model made. A product the grader expects
but the tool never returns means the tool (category / keyword / parts filter) is hiding it; a product the tool returns first
that the grader does not expect is probably a part or accessory.

Run from the repo folder:  python diagnose_retest.py
"""
from __future__ import annotations

from eval_retest import CASES
from sjbot.db import run_query
from sjbot.tools.search_products import search_products

WANTED = ("retest-typo-dresser", "retest-parts-bed", "retest-refine-dining")

# the searches the model actually made in eval_20261023_162126 (and the one it should have made)
SEARCHES = {
    "retest-typo-dresser": [
        {"category_slug": "bedroom-furniture-dressers-and-chests", "sort": "price_asc", "keyword": "dresser"},
        {"sort": "price_asc", "keyword": "dresser"},                                  # no category
    ],
    "retest-parts-bed": [
        {"category_slug": "bedroom-furniture-beds", "sort": "price_asc", "keyword": "queen"},
        {"category_slug": "bedroom-furniture-beds", "sort": "price_asc", "keyword": "queen bed"},
        {"sort": "price_asc", "keyword": "queen bed"},
    ],
    "retest-refine-dining": [
        {"keyword": "dining table", "max_price": 500, "sort": "price_asc"},           # no category
        {"category_slug": "dining-room-dining-furniture", "sort": "price_asc", "keyword": "table", "max_price": 500},
    ],
}


def categories_of(name: str) -> str:
    rows = run_query(
        "SELECT DISTINCT cc.slug FROM vw_chat_products p "
        "JOIN vw_chat_product_categories cpc ON cpc.product_uuid = p.product_uuid "
        "JOIN vw_chat_categories cc ON cc.category_uuid = cpc.category_uuid WHERE p.name = %s", [name])
    return ", ".join(r["slug"] for r in rows) or "(no category)"


for case in CASES:
    if case["id"] not in WANTED:
        continue
    t = case["truth"]
    print("=" * 100)
    print(case["id"], "| asked:", " | ".join(case["turns"]))
    print("-- grader expects (up to 8):")
    for r in run_query(t["sql"], t.get("params", []))[:8]:
        print(f"   {r[t['col']]!r:60} ${r[t['price_col']]}   [{categories_of(r[t['col']])}]")
    for args in SEARCHES[case["id"]]:
        out = search_products(args)
        print(f"-- search_products({args})")
        if "error" in out:
            print("   ERROR:", out["error"])
            continue
        for p in out["products"][:6]:
            print(f"   {p['name']!r:60} ${p['price']}   [{categories_of(p['name'])}]")
        if not out["products"]:
            print("   (no rows)")
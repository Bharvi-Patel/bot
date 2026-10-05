"""Phase 2 check: the 12 product and store questions from Appendix D, answered by the tools alone.

For each question it makes the tool call(s) a model would make, then checks the result against an
independent query on the same views. Nothing here calls an LLM and nothing contains customer data.

  python eval_phase2.py
  python eval_phase2.py --loreo "Loreo Sofa" --adlai "Adlai Sofa"     (change the names if yours differ)
  python eval_phase2.py --mattress-slug some-category-slug              (pick the mattress category yourself)
Each question can take 10 to 30 seconds. A "running" line is printed first so you can see it is working.

Statuses:  PASS    the tool output meets the pass condition
           REVIEW  the tools behaved, but the data means the bot's answer must be "not found/unknown";
                   or a number differs from the doc and you should look at it
           FAIL    the tool output is wrong
           ERROR   the check itself crashed (usually a missing view or grant)
Set the SJ_DB_* variables first (see .env.example).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime

from sjbot.db import run_query as rq
from sjbot.tools.check_inventory import check_inventory
from sjbot.tools.get_product_details import get_product_details
from sjbot.tools.get_shipping_options import get_shipping_options
from sjbot.tools.get_store_info import get_store_info
from sjbot.tools.list_brands import list_brands
from sjbot.tools.list_categories import list_categories
from sjbot.tools.search_products import search_products

SOFA_SLUG = "sofas-and-seating-sofas"
PASS, REVIEW, FAIL, ERROR = "PASS", "REVIEW", "FAIL", "ERROR"


_FOUND: dict[str, dict | None] = {}


def find_product(keyword: str) -> dict | None:
    """First product whose name contains every word of `keyword` (searched once, then remembered)."""
    if keyword not in _FOUND:
        out = search_products({"keyword": keyword})
        _FOUND[keyword] = out["products"][0] if out.get("products") else None
    return _FOUND[keyword]


ACCESSORY_WORDS = "protector topper pad cover pillow sheet sheets foundation frame"


def subtree_sql() -> str:
    return (
        "WITH RECURSIVE sub AS (\n"
        "  SELECT category_id, category_uuid FROM vw_chat_categories WHERE slug = %s\n"
        "  UNION ALL\n"
        "  SELECT c.category_id, c.category_uuid FROM vw_chat_categories c JOIN sub ON c.parent_id = sub.category_id\n"
        ")\n"
    )


# ---------------------------------------------------------------- questions
def q7():
    out = get_shipping_options({})
    if not out.get("found"):
        return REVIEW, "no shipping options on file: answer must be 'not confirmed'"
    methods = [(z["zone"], m["method"], m["price"], m["note"]) for z in out["zones"] for m in z["methods"]]
    pickup = [m for m in methods if "pick" in f"{m[1]} {m[3] or ''}".lower()]
    if pickup:
        z, name, price, note = pickup[0]
        return PASS, f"pickup found: {name} in {z}, price {price}" + (f", note: {note}" if note else "")
    return REVIEW, "no pickup method listed (methods: " + ", ".join(sorted({m[1] for m in methods})) + "): bot must not claim pickup"


def q8():
    out = get_store_info({})
    store = out["stores"][0]
    raw = rq("SELECT opening_hours FROM vw_chat_store LIMIT 1")[0]["opening_hours"]
    hours = json.loads(raw) if isinstance(raw, str) else raw
    if not store.get("hours"):
        return REVIEW, f"hours not in the expected JSON form: {store.get('hours_text')!r}"
    bad = []
    for line in store["hours"]:
        entry = hours.get(line["day"].lower(), {})
        if entry.get("is_open") is False:
            expected = "Closed"
        else:
            f = lambda s: datetime.strptime(s, "%H:%M").strftime("%I:%M %p").lstrip("0")
            expected = f"{f(entry['open'])} - {f(entry['close'])}"
        if line["hours"] != expected:
            bad.append((line["day"], line["hours"], expected))
    if bad:
        return FAIL, f"mismatch with stored hours: {bad}"
    return PASS, store["hours_summary"]


def q9():
    store = get_store_info({})["stores"][0]
    if store.get("address") and store.get("phone"):
        return PASS, f"{store['address']} | {store['phone']}"
    return REVIEW, f"address or phone missing: address={store.get('address')!r} phone={store.get('phone')!r}"


def q11():
    out = search_products({"category_slug": SOFA_SLUG, "max_price": 1000, "sort": "price_desc"})
    rows = out["products"]
    if not rows:
        return FAIL, "no sofas found under $1,000"
    over = [r["sku"] for r in rows if r["price"] > 1000]
    n = rq(subtree_sql() +
           "SELECT COUNT(DISTINCT p.product_uuid) AS n FROM sub "
           "JOIN vw_chat_product_categories pc ON pc.category_uuid = sub.category_uuid "
           "JOIN vw_chat_product_search p ON p.product_uuid = pc.product_uuid WHERE p.price <= 1000",
           [SOFA_SLUG])[0]["n"]
    if over:
        return FAIL, f"over $1,000: {over}"
    note = "matches the doc's 166" if n == 166 else f"independent count is {n}; the doc says 166"
    return PASS, f"{len(rows)} shown, highest ${rows[0]['price']:.2f}; {note}"


def q12():
    cats = list_categories({"keyword": "loveseat"}).get("matches", [])
    if not cats:
        return REVIEW, "no category contains 'loveseat'"
    slug = cats[0]["slug"]
    out = search_products({"category_slug": slug, "color": "Black"})
    if not out["products"]:
        return REVIEW, f"no black products in '{slug}': bot must say none found"
    problems = []
    for p in out["products"]:
        d = get_product_details({"sku": p["sku"]})
        colors = d.get("attributes", {}).get("Color", []) if d.get("found") else None
        if colors is None:
            problems.append((p["sku"], "not found"))
        elif "Black" not in colors:
            problems.append((p["sku"], f"color is {colors}"))
    if problems:
        return FAIL, f"results that are not real black products: {problems}"
    return PASS, f"{len(out['products'])} real products in '{slug}', e.g. {out['products'][0]['name']}"


def q13():
    out = list_brands({})
    names = [b["name"] for b in out.get("brands", [])]
    if len(names) != len({n.lower() for n in names}):
        return FAIL, "duplicate brand names"
    live = [b["name"] for b in out["brands"] if b["products_available"] > 0]
    if len(names) == 19:
        return PASS, f"19 brands, no duplicates; {len(live)} have current products"
    return REVIEW, f"{len(names)} brands (doc says 19), no duplicates; {len(live)} have current products"


def q14(slug_override: str | None):
    matches = list_categories({"keyword": "mattress"}).get("matches", [])
    if slug_override:
        slug = slug_override
    elif matches:
        exact = [m for m in matches if m["title"].lower() in ("mattress", "mattresses")]
        slug = (exact or matches)[0]["slug"]
    else:
        return REVIEW, "no category contains 'mattress'"
    candidates = ", ".join(m["slug"] for m in matches[:5])
    values = [r["value"] for r in rq("SELECT DISTINCT value FROM vw_chat_product_attributes WHERE group_name = 'Bed Size'")]
    queen = next((v for v in values if v.lower() == "queen"), None) or next((v for v in values if "queen" in v.lower()), None)
    filters = {"category_slug": slug, "exclude_keyword": ACCESSORY_WORDS, "sort": "price_asc"}
    if queen:
        filters.update({"attribute_group": "Bed Size", "attribute_value": queen})
        how = f"Bed Size = {queen}"
    else:
        filters["keyword"] = "queen"
        how = "name contains 'queen'"
    out = search_products(filters)
    if not out["products"]:
        return REVIEW, f"no queen mattresses found in '{slug}' ({how}); mattress categories seen: {candidates}"
    top = out["products"][:3]
    tool_min = top[0]["price"]
    excl = " AND ".join("p.name NOT LIKE %s" for _ in ACCESSORY_WORDS.split())
    excl_params = [f"%{w}%" for w in ACCESSORY_WORDS.split()]
    cat_sql = ("(p.product_uuid IN (SELECT pc.product_uuid FROM vw_chat_product_categories pc JOIN sub ON sub.category_uuid = pc.category_uuid) "
               " OR p.parent_uuid IN (SELECT pc.product_uuid FROM vw_chat_product_categories pc JOIN sub ON sub.category_uuid = pc.category_uuid))")
    if queen:
        sql = (subtree_sql() + f"SELECT MIN(p.price) AS m FROM vw_chat_product_search p WHERE {cat_sql} AND {excl} "
               "AND p.product_uuid IN (SELECT a.product_uuid FROM vw_chat_product_attributes a WHERE a.group_name = 'Bed Size' AND a.value = %s)")
        params = [slug] + excl_params + [queen]
    else:
        sql = (subtree_sql() + f"SELECT MIN(p.price) AS m FROM vw_chat_product_search p WHERE p.name LIKE '%%queen%%' AND {cat_sql} AND {excl}")
        params = [slug] + excl_params
    real_min = float(rq(sql, params)[0]["m"])
    names = " | ".join(f"{p['name']} ${p['price']:.2f}" for p in top)
    if abs(tool_min - real_min) >= 0.005:
        return FAIL, f"tool says ${tool_min:.2f} but the independent minimum is ${real_min:.2f}"
    return REVIEW, (f"category '{slug}' ({how}), accessories excluded; cheapest 3: {names}. Price agrees with the independent "
                    f"minimum, but READ the names: are they real queen mattresses? (other mattress categories: {candidates})")


def q15(name: str):
    p = find_product(name)
    if not p:
        return REVIEW, f"no product matching '{name}' in this catalog: bot must say it can't find it"
    d = get_product_details({"sku": p["sku"]})
    real = float(rq("SELECT price FROM vw_chat_product_search WHERE sku = %s LIMIT 1", [p["sku"]])[0]["price"])
    if d.get("found") and abs(d["product"]["price"] - real) < 0.005:
        return PASS, f"{d['product']['name']}: ${d['product']['price']:.2f} (equals sale_price)"
    return FAIL, f"tool price {d.get('product', {}).get('price')} vs sale_price {real}"


def q16(name: str):
    p = find_product(name)
    if not p:
        return REVIEW, f"no product matching '{name}' in this catalog: bot must say it can't find it"
    d = get_product_details({"sku": p["sku"]})
    tool_mat = d["attributes"].get("Material")
    real = [r["value"] for r in rq(
        "SELECT a.value FROM vw_chat_product_attributes a JOIN vw_chat_product_search s ON s.product_uuid = a.product_uuid "
        "WHERE s.sku = %s AND a.group_name = 'Material'", [p["sku"]])]
    if (tool_mat or []) == list(dict.fromkeys(real)):
        return PASS, f"{d['product']['name']}: material " + (", ".join(tool_mat) if tool_mat else "not listed (bot must say so)")
    return FAIL, f"tool says {tool_mat}, view says {real}"


def q17():
    out = search_products({"category_slug": SOFA_SLUG, "max_width_in": 80})
    rows = out["products"]
    if not rows:
        return REVIEW, "no sofas with a recorded width up to 80 inches"
    bad = [(r["sku"], r["width"]) for r in rows if not r["width"] or r["width"] > 80]
    if bad:
        return FAIL, f"widths outside 1-80: {bad}"
    if "without_size_data" not in out:
        return FAIL, "tool did not report products with no size data"
    n = out["without_size_data"]
    return PASS, f"{len(rows)} shown, all 80 in. or less; {n} more sofa families have no width on file" + (" (tool tells the bot to say so)" if n else "")


def q18(name: str):
    p = find_product(name)
    sku = p["sku"] if p else "UNKNOWN-SKU"
    out = check_inventory({"sku": sku})
    if out.get("availability") == "unknown" and "contact the store" in out["message"]:
        return PASS, "availability unknown, customer told to contact the store"
    return FAIL, f"unexpected: {out}"


def q28():
    zones = [z["zone"] for z in get_shipping_options({}).get("zones", [])]
    canada = get_shipping_options({"zone_keyword": "canada"})
    if canada.get("found"):
        return PASS, f"a Canada zone exists: {canada['zones'][0]['zone']}"
    return PASS, f"no Canada zone (zones on file: {', '.join(zones) or 'none'}): shipping to Canada is not confirmed"


QUESTIONS = [
    (7, "Is in-store pickup available?", lambda a: q7()),
    (8, "What are your store hours?", lambda a: q8()),
    (9, "Where are you located?", lambda a: q9()),
    (11, "Show me sofas under $1,000", lambda a: q11()),
    (12, "Do you have a black loveseat?", lambda a: q12()),
    (13, "What brands do you carry?", lambda a: q13()),
    (14, "Cheapest queen mattress", lambda a: q14(a.mattress_slug)),
    (15, "What is the price of the Loreo Sofa?", lambda a: q15(a.loreo)),
    (16, "What material is the Adlai Sofa?", lambda a: q16(a.adlai)),
    (17, "Will a sofa fit on an 80 inch wall?", lambda a: q17()),
    (18, "Is the Adlai Sofa in stock?", lambda a: q18(a.adlai)),
    (28, "Do you ship to Canada?", lambda a: q28()),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loreo", default="Loreo Sofa")
    ap.add_argument("--adlai", default="Adlai Sofa")
    ap.add_argument("--mattress-slug", default=None, help="category slug to use for the mattress question")
    ap.add_argument("--trace", action="store_true", help="print the full error for any ERROR")
    args = ap.parse_args()

    results = []
    for num, question, fn in QUESTIONS:
        print(f"Q{num} running: {question} ...", flush=True)
        t0 = time.time()
        try:
            status, evidence = fn(args)
        except KeyboardInterrupt:
            print("Stopped by you (Ctrl+C). Partial results are not saved.")
            return 2
        except Exception as exc:  # a crashed check is reported, not hidden
            status, evidence = ERROR, f"{type(exc).__name__}: {exc}"
            if "command denied" in str(exc):
                evidence += "  -> re-run create_chatbot_user.sql (the latest copy) so chatbot_ro has this grant"
            if args.trace:
                traceback.print_exc()
        results.append({"q": num, "question": question, "status": status, "evidence": evidence})
        print(f"Q{num:<2} {status:<6} {question}  ({time.time() - t0:.1f}s)\n      {evidence}\n", flush=True)

    counts = {s: sum(r["status"] == s for r in results) for s in (PASS, REVIEW, FAIL, ERROR)}
    print("Summary:", ", ".join(f"{v} {k}" for k, v in counts.items()), f"(of {len(results)})")
    with open("eval_phase2_results.json", "w", encoding="utf-8") as fh:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"), "results": results}, fh, indent=2, ensure_ascii=False)
    print("Saved eval_phase2_results.json")
    return 0 if not (counts[FAIL] or counts[ERROR]) else 1


if __name__ == "__main__":
    sys.exit(main())

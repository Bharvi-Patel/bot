"""Phase 4 check: the product recommendation questions from Appendix D (19, 20, 22), run through recommend_products.

  python eval_phase4.py
Needs SJ_DB_* (read-only MySQL views, as in Phase 2) and RAG_DB_URL (Postgres). The first run loads the embedding model.
For each question it makes the tool call a model would make (hard filters already extracted), then checks:
  - every price equals the live sale_price in vw_chat_products (no hallucinated prices)
  - the budget (Q19: $800) holds on the live price
  - the category was actually indexed (otherwise REVIEW: run build_products.py for it)
Whether the products are SENSIBLE is your judgment: read the names it prints.

Statuses: PASS (mechanical checks hold, now read the names) | REVIEW (category not indexed, nothing found, or suspicious names)
          | FAIL (a price or budget is wrong) | ERROR (the search crashed)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime

from sjbot.db import run_query as rq
from sjbot.tools.list_categories import list_categories
from sjbot.tools.recommend_products import CUTOFF, recommend_products
from sjbot.tools.search_policies import _default_run_query

PASS, REVIEW, FAIL, ERROR = "PASS", "REVIEW", "FAIL", "ERROR"
SOFA_SLUG = "sofas-and-seating-sofas"
ACCESSORY_WORDS = ("protector", "topper", "pad", "cover", "pillow", "sheet", "foundation", "frame")

# (Appendix D #, question, tool arguments a model would send, category keyword to resolve, budget cap)
CASES = [
    (19, "Recommend a sofa for a small apartment under $800",
     {"query": "compact sofa for a small apartment", "category_slug": SOFA_SLUG, "max_price": 800}, None, 800),
    (20, "I need a desk for a small home office",
     {"query": "desk for a small home office, compact"}, "desk", None),
    (22, "Best mattress for back pain?",
     {"query": "supportive mattress firmness and support for back", "limit": 5}, "mattress", None),
]


def resolve_category(keyword: str) -> str | None:
    matches = list_categories({"keyword": keyword}).get("matches", [])
    exact = [m for m in matches if m["title"].lower() in (keyword, keyword + "s")]
    return (exact or matches or [{}])[0].get("slug")


def run_case(num, question, args, keyword, cap):
    args = dict(args)
    if keyword:
        slug = resolve_category(keyword)
        if not slug:
            return REVIEW, f"no category contains '{keyword}'"
        args["category_slug"] = slug
    slug = args.get("category_slug")
    n = _default_run_query("SELECT COUNT(*) AS n FROM rag_chunks WHERE source_type='product' AND %s = ANY(category_slugs)", [slug])[0]["n"]
    if n == 0:
        return REVIEW, f"category '{slug}' has no product documents yet: python build_products.py --slug {slug}  then  python sync_products.py (in rag\\)"
    out = recommend_products(args)
    if "error" in out:
        return ERROR, out["error"]
    if not out["found"]:
        return REVIEW, f"nothing found in '{slug}' ({n} documents); best similarity {out['best_similarity']} vs cutoff {CUTOFF}; dropped by live check: {out['dropped_by_live_check']}"
    problems = []
    for p in out["products"]:
        row = rq("SELECT price FROM vw_chat_products WHERE sku = %s LIMIT 1", [p["sku"]])
        if not row or abs(float(row[0]["price"]) - p["price"]) >= 0.005:
            problems.append((p["sku"], p["price"], row[0]["price"] if row else "not in view"))
        if cap is not None and p["price"] > cap:
            problems.append((p["sku"], f"over budget {p['price']}"))
    shown = " | ".join(f"{p['name']} ${p['price']:.2f} (sim {p['similarity']})" for p in out["products"])
    if problems:
        return FAIL, f"price or budget problem: {problems}"
    flagged = [p["name"] for p in out["products"] if any(w in p["name"].lower() for w in ACCESSORY_WORDS)] if num == 22 else []
    if flagged:
        return REVIEW, f"prices are live and correct, but these look like accessories, not mattresses: {flagged}. All: {shown}"
    return PASS, f"{out['count']} products, prices equal live sale_price, dropped by live check: {out['dropped_by_live_check']}. READ: {shown}"


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    print(f"product cutoff {CUTOFF}\n")
    results = []
    for num, question, args, keyword, cap in CASES:
        print(f"Q{num} running: {question} ...", flush=True)
        try:
            status, evidence = run_case(num, question, args, keyword, cap)
        except KeyboardInterrupt:
            print("Stopped (Ctrl+C)."); return 2
        except Exception as exc:
            status, evidence = ERROR, f"{type(exc).__name__}: {exc}"
        results.append({"q": num, "question": question, "status": status, "evidence": evidence})
        print(f"Q{num:<2} {status:<6} {question}\n      {evidence}\n", flush=True)
    counts = {s: sum(r["status"] == s for r in results) for s in (PASS, REVIEW, FAIL, ERROR)}
    print("Summary:", ", ".join(f"{v} {k}" for k, v in counts.items()), f"(of {len(results)})")
    with open("eval_phase4_results.json", "w", encoding="utf-8") as fh:
        json.dump({"run_at": datetime.now().isoformat(timespec="seconds"), "cutoff": CUTOFF, "results": results}, fh, indent=2, ensure_ascii=False)
    print("Saved eval_phase4_results.json")
    return 0 if not (counts[FAIL] or counts[ERROR]) else 1


if __name__ == "__main__":
    sys.exit(main())

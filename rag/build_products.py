"""Phase 4: compose product documents (data map 5.4) for the categories you choose and write products.jsonl.

Reads MySQL ONLY through the read-only vw_chat_* views, as chatbot_ro (same SJ_DB_* variables as the Phase 2 tools).
Writes rows in the rag_chunks shape with source_type='product'. Nothing is embedded here; sync_products.py does that.

  python build_products.py --slug sofas-and-seating-sofas        start with one category (map: check retrieval first)
  python build_products.py --keyword desk                        resolve categories whose title contains 'desk'
  python build_products.py --slug a --slug b --limit 50          several categories; --limit for a quick trial
Only parent products (product_type = 0) are indexed; variants come later, once the parent documents work.
"""
import argparse, json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so sjbot is importable

from sjbot.db import run_query as rq
from sjbot.product_docs import compose_document, content_hash, descendants, slugs_with_ancestors

URL = os.environ.get("SJ_PRODUCT_URL", "/product/{slug}")        # ASSUMED storefront route: set SJ_PRODUCT_URL to the real one
BATCH = 400


def marks(n): return ",".join(["%s"] * n)


def batches(seq, n=BATCH):
    for i in range(0, len(seq), n): yield seq[i:i + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", default=[], help="category slug (its sub-categories are included)")
    ap.add_argument("--keyword", action="append", default=[], help="every category whose title contains this word")
    ap.add_argument("--limit", type=int, default=0, help="stop after N products (trial runs)")
    args = ap.parse_args()
    if not (args.slug or args.keyword):
        sys.exit("give --slug or --keyword (start with one category, e.g. --slug sofas-and-seating-sofas)")

    t0 = time.time()
    cats = rq("SELECT category_id, category_uuid, parent_id, slug, title FROM vw_chat_categories")
    by_uuid, by_id = {c["category_uuid"]: c for c in cats}, {c["category_id"]: c for c in cats}
    roots = list(args.slug)
    for kw in args.keyword:
        hits = [c["slug"] for c in cats if kw.lower() in (c["title"] or "").lower()]
        print(f"keyword '{kw}' -> {hits or 'no category'}"); roots += hits
    scope, missing = descendants(cats, roots)
    if missing: print("unknown category slug(s):", missing)
    if not scope: sys.exit("no categories matched")
    print(f"{len(roots)} root categories, {len(scope)} including sub-categories: {sorted({c['slug'] for c in scope})[:12]}")

    scope_uuids = [c["category_uuid"] for c in scope]
    in_scope = set()
    for b in batches(scope_uuids):
        in_scope.update(r["product_uuid"] for r in rq(f"SELECT DISTINCT product_uuid FROM vw_chat_product_categories WHERE category_uuid IN ({marks(len(b))})", b))
    uuids = sorted(in_scope)
    print(f"{len(uuids)} products linked to those categories ({time.time()-t0:.0f}s)")

    products = []
    for b in batches(uuids):
        products += rq("SELECT product_uuid, sku, slug, name, short_description, full_description, special_features, price, "
                       "brand_slug, brand_name, width, depth, height, lifestyle FROM vw_chat_products "
                       f"WHERE product_type = 0 AND product_uuid IN ({marks(len(b))})", b)
        if args.limit and len(products) >= args.limit: products = products[:args.limit]; break
    print(f"{len(products)} active, priced parent products ({time.time()-t0:.0f}s)")
    puuids = [p["product_uuid"] for p in products]

    links, attrs = {}, {}
    for b in batches(puuids):
        for r in rq(f"SELECT product_uuid, category_uuid FROM vw_chat_product_categories WHERE product_uuid IN ({marks(len(b))})", b):
            links.setdefault(r["product_uuid"], []).append(r["category_uuid"])
        for r in rq(f"SELECT product_uuid, group_name, value FROM vw_chat_product_attributes WHERE product_uuid IN ({marks(len(b))})", b):
            attrs.setdefault(r["product_uuid"], {}).setdefault(r["group_name"], []).append(r["value"])
    print(f"links and attributes loaded ({time.time()-t0:.0f}s)")

    n = 0
    with open("products.jsonl", "w", encoding="utf8") as fh:
        for p in products:
            linked = [by_uuid[u] for u in links.get(p["product_uuid"], []) if u in by_uuid]
            content = compose_document(p, [c["title"] for c in linked], attrs.get(p["product_uuid"], {}))
            slugs = sorted({s for c in linked for s in slugs_with_ancestors(c["category_uuid"], by_uuid, by_id)})
            row = dict(source_type="product", source_key=p["product_uuid"], title=p["name"], heading=p["name"],
                       url_path=URL.format(slug=p["slug"]), brand_slug=p["brand_slug"], category_slugs=slugs,
                       price=float(p["price"]), content=content, content_hash=content_hash(content), embedding=None)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n"); n += 1
    json.dump({"roots": sorted(set(roots))}, open("products_scope.json", "w"))
    print(f"wrote {n} documents to products.jsonl ({time.time()-t0:.0f}s). Sample:\n")
    print(open("products.jsonl", encoding="utf8").readline()[:900])


if __name__ == "__main__":
    main()

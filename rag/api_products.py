"""Build products.jsonl for ONE store from its storefront API (no MySQL), replacing rag/build_products.py for new stores.

  python rag/api_products.py <store-uuid> --slug mattresses-and-bedding --slug home-office-and-study-desks
  python rag/api_products.py <store-uuid> --keyword desk --limit 50
  python rag/sync_products.py <store-uuid>          (embeds and loads products.jsonl, as before)

Differences from build_products.py:
  * Category slugs come from the API's category tree, so they match what list_categories shows the model.
  * Products whose price depends on an option (product_type 1, e.g. mattress sizes) ARE indexed. Their own price is
    -0.01 in the API, so the document lists the per-option prices and the row's price is the lowest option price.
  * source_key is the product slug (the API gives no product uuid), so the first run replaces rows keyed by the old uuid.
Nothing is dropped silently: products with no usable price or no name are printed with the reason.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))      # project root, so bot is importable

from bot.product_docs import compose_document, content_hash

URL = os.environ.get("SJ_PRODUCT_URL", "/product/{slug}")        # ASSUMED storefront route: set SJ_PRODUCT_URL to the real one
MAX_PAGES = 200


def flatten(tree: list[dict]) -> list[dict]:
    """The nested /categories/tree as flat rows with parent_slug."""
    out: list[dict] = []

    def walk(nodes, parent):
        for n in nodes or []:
            out.append({"slug": n.get("slug"), "name": n.get("name"), "parent_slug": parent})
            walk(n.get("children"), n.get("slug"))

    walk(tree, None)
    return out


def scope_from_tree(tree: list[dict], roots: list[str], keywords: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(root slugs, unknown slugs, all slugs in scope including sub-categories)."""
    flat = flatten(tree)
    by_slug = {c["slug"]: c for c in flat}
    kids: dict = {}
    for c in flat:
        kids.setdefault(c["parent_slug"], []).append(c["slug"])
    chosen = [s for s in roots if s in by_slug]
    missing = [s for s in roots if s not in by_slug]
    for kw in keywords:
        chosen += [c["slug"] for c in flat if kw.lower() in (c["name"] or "").lower()]
    chosen = list(dict.fromkeys(chosen))
    seen: list[str] = []
    stack = list(chosen)
    while stack:
        s = stack.pop()
        if s in seen:
            continue
        seen.append(s)
        stack.extend(kids.get(s, []))
    return chosen, missing, seen


def ancestors(slug: str, by_slug: dict[str, dict]) -> list[str]:
    out, guard = [], 0
    while slug and slug in by_slug and guard < 20:
        out.append(slug)
        slug, guard = by_slug[slug]["parent_slug"], guard + 1
    return out


def option_prices(detail: dict) -> list[float]:
    return [o["price"] for o in detail.get("options") or [] if isinstance(o.get("price"), (int, float)) and o["price"] > 0]


def product_row(detail: dict, by_slug: dict[str, dict], url_template: str = URL,
                brand_slugs: dict[str, str] | None = None) -> tuple[dict | None, str | None]:
    """(row, None) or (None, reason it was left out). brand_slugs maps brand name (lowercase) to the API's brand slug."""
    if not detail.get("name"):
        return None, "no name"
    prices = option_prices(detail)
    base = detail.get("price")
    if detail.get("price_varies"):
        if not prices:
            return None, "price depends on an option but no option has a price"
        price = min(prices)
    elif isinstance(base, (int, float)) and base > 0:
        price = base
    else:
        return None, "no usable price"

    cats = detail.get("categories") or []
    slugs = sorted({s for c in cats for s in ancestors(c.get("slug"), by_slug)})
    attrs: dict[str, list[str]] = {}
    for o in detail.get("options") or []:
        for group, value in (o.get("attributes") or {}).items():
            attrs.setdefault(group, []).append(value)
    dims = detail.get("dimensions") or {}
    # A product priced by option has no SKU of its own: show its options' SKUs so a SKU question can still find it.
    sku = detail.get("sku") or ", ".join(str(o["sku"]) for o in (detail.get("options") or [])[:4] if o.get("sku")) or "n/a"
    p = {"name": detail["name"], "brand_name": detail.get("brand"), "sku": sku, "lifestyle": detail.get("lifestyle"),
         "short_description": detail.get("short_description"), "full_description": detail.get("description"),
         "special_features": detail.get("special_features"),
         "width": dims.get("width"), "depth": dims.get("depth"), "height": dims.get("height")}
    content = compose_document(p, [c.get("name") for c in cats], attrs)
    if detail.get("price_varies"):
        lines = []
        for o in detail.get("options") or []:
            if isinstance(o.get("price"), (int, float)) and o["price"] > 0:
                label = ", ".join(f"{v}" for v in (o.get("attributes") or {}).values()) or str(o.get("sku") or "option")
                lines.append(f"{label}: ${o['price']:,.2f}")
        content += "\nPrice by option: " + "; ".join(lines)
    # The API's own brand slug (e.g. "foa"), so the brand filter matches list_brands. Slugified name only as a fallback.
    name = (detail.get("brand") or "").strip().lower()
    brand_slug = (brand_slugs or {}).get(name) or name.replace(" ", "-") or None
    row = dict(source_type="product", source_key=detail["slug"], title=detail["name"], heading=detail["name"],
               url_path=url_template.format(slug=detail["slug"]), brand_slug=brand_slug, category_slugs=slugs,
               price=float(price), content=content, content_hash=content_hash(content), embedding=None)
    return row, None


def listing_slugs(client, category_slugs: list[str], limit: int = 0) -> list[str]:
    seen: dict[str, None] = {}
    for cat in category_slugs:
        page = 1
        while page <= MAX_PAGES:
            res = client.category_products(cat, page)
            for p in res.get("products") or []:
                if p.get("slug"):
                    seen.setdefault(p["slug"])
            if limit and len(seen) >= limit:
                return list(seen)[:limit]
            if page >= (res.get("last_page") or 1):
                break
            page += 1
    return list(seen)


def ingest(client, roots: list[str], keywords: list[str] = (), limit: int = 0,
           skipped: list | None = None, notes: list | None = None) -> list[dict]:
    tree = client.categories()
    by_slug = {c["slug"]: c for c in flatten(tree)}
    chosen, missing, scope = scope_from_tree(tree, list(roots), list(keywords))
    if notes is not None and missing:
        notes.append(f"unknown category slug(s): {missing}")
    try:
        brand_slugs = {(b.get("name") or "").strip().lower(): b["slug"] for b in client.brands() if b.get("slug")}
    except Exception as exc:
        brand_slugs = {}
        if notes is not None:
            notes.append(f"brands lookup failed ({exc}); brand slugs are guessed from names")
    rows = []
    for slug in listing_slugs(client, scope, limit):
        try:
            detail = client.product_detail(slug)
        except Exception as exc:  # one bad product must not stop the run, but it is reported
            if skipped is not None:
                skipped.append((slug, f"detail call failed: {exc}"))
            continue
        row, why = product_row(detail, by_slug, brand_slugs=brand_slugs)
        if row:
            rows.append(row)
        elif skipped is not None:
            skipped.append((slug, why))
    return rows


def main(argv: list[str] | None = None) -> None:
    import argparse
    from bot.api_client import ApiClient, ApiConfig
    from bot.store_config import valid_store_id
    ap = argparse.ArgumentParser(description="Write products.jsonl for one store from its storefront API.")
    ap.add_argument("store_uuid")
    ap.add_argument("--slug", action="append", default=[], help="category slug (its sub-categories are included)")
    ap.add_argument("--keyword", action="append", default=[], help="every category whose name contains this word")
    ap.add_argument("--limit", type=int, default=0, help="stop after N products (trial runs)")
    args = ap.parse_args(argv)
    if not (args.slug or args.keyword):
        ap.error("give --slug or --keyword")
    if not valid_store_id(args.store_uuid):
        ap.error("store_uuid has characters that are not allowed")
    skipped: list = []
    notes: list = []
    rows = ingest(ApiClient(ApiConfig.from_env(args.store_uuid)), args.slug, args.keyword, args.limit, skipped, notes)
    with open("products.jsonl", "w", encoding="utf8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # sync_products.py deletes indexed products inside these roots that are missing from products.jsonl. A trial run
    # (--limit) or a keyword-only run holds only part of a category, so it gets NO roots and can never delete anything.
    roots = [] if (args.limit or args.keyword) else sorted(args.slug)
    json.dump({"roots": roots}, open("products_scope.json", "w"))
    print(f"wrote {len(rows)} products for store {args.store_uuid}")
    if not roots:
        print("  NOTE: partial run, so the scope is empty: sync_products will add and update but remove nothing.")
    for n in notes:
        print("  NOTE:", n)
    for slug, why in skipped:
        print(f"  left out: {slug}: {why}")


if __name__ == "__main__":
    main()

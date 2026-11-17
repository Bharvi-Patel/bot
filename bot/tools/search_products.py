"""search_products: find products through the store API (POST /products/search and POST /categories/{slug}/products).

The model only chooses filter values; this tool decides which API calls to make.

What the store API is known to do (from saved real responses): keyword search with {"q": ...}, and a product
listing for one category. How brand, price range, sort and attributes are sent to the API is NOT known yet, so:
  - brand, price limits and price sorting are applied here, on the pages fetched (at most MAX_PAGES x 16 products);
  - when a request needs the whole match list (sorting by price) and there are more matches than that, the tool
    says so instead of answering from part of the list;
  - color, material, attribute and width filters cannot be applied at all, and the tool says that.
Once the API's filter format is known, these local steps can be replaced by API parameters.
"""
from __future__ import annotations

import re
from typing import Any

from bot.api_client import ApiClient, ApiError, get_client
from bot.tools.list_categories import rows_from_tree

MAX_RESULTS = 8   # hard cap, not a parameter
MAX_PAGES = 5     # API pages (16 products each) fetched for one search

# Sort choices the model may send. "name" is the store's own order: the API's order is not known to be by name.
SORTS = {
    "name": "store order",
    "price_asc": "cheapest first",
    "price_desc": "most expensive first",
}

# Parts are sold only as pieces of a bigger item ("Base, Box 2 of 2") and were once listed to customers as if they were
# products (a "Queen Decking Board Set" as the cheapest bed). They stay out of results unless the customer's search is
# explicitly for that category. Add a pattern when a new part shows up as "the cheapest <item>".
HIDE_COMPONENT_SKUS = True
COMPONENT_CATEGORY = "component-skus"
COMPONENT_NAME_PATTERNS = [re.compile(p, re.I) for p in (
    r"Box . of .",        # "Base, Box 2 of 2"
    r"Decking Board",     # "Queen Decking Board Set"
    r" Dresser Top",      # "Akerson Dresser Top Grey": the top piece of a mirrored dresser set
    r" Table Top$",       # names that END in "Table Top": a loose top, not a table
)]

ALLOWED_KEYS = {
    "keyword", "category_slug", "brand_slug", "min_price", "max_price",
    "color", "material", "attribute_group", "attribute_value", "max_width_in", "sort",
}

# Attribute groups that exist in the catalog (from the old database snapshot; also used by list_attribute_values).
ATTRIBUTE_GROUPS = [
    "Bar Features", "Bed Size", "Color", "Frame Size", "Item Type", "Leaf Type", "Material",
    "Pattern", "Rug Size", "Shape", "Table Base", "Table Shape", "Table Top",
]

# Filters the store API cannot be asked for yet (format unknown), keyed by the argument the model sends.
UNSUPPORTED = {
    "color": "color", "material": "material", "attribute_group": "attribute filters",
    "attribute_value": "attribute filters", "max_width_in": "size or width limits",
}


class ToolInputError(ValueError):
    """The model sent parameters this tool won't accept."""


def _number(args: dict, key: str) -> float | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"{key} must be a number")
    if value < 0:
        raise ToolInputError(f"{key} cannot be negative")
    return float(value)


def _text(args: dict, key: str, max_len: int = 100) -> str | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be text")
    value = value.strip()
    if len(value) > max_len:
        raise ToolInputError(f"{key} is too long")
    return value or None


def _prepare(args: dict[str, Any]) -> dict[str, Any]:
    """Validate everything the model sent and return clean values."""
    unknown = set(args) - ALLOWED_KEYS
    if unknown:
        raise ToolInputError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    vals: dict[str, Any] = {
        "keyword": _text(args, "keyword"),
        "category_slug": _text(args, "category_slug"),
        "brand_slug": _text(args, "brand_slug"),
        "min_price": _number(args, "min_price"),
        "max_price": _number(args, "max_price"),
        "sort": args.get("sort") or "name",
    }
    group, value = _text(args, "attribute_group"), _text(args, "attribute_value")
    if bool(group) != bool(value):
        raise ToolInputError("attribute_group and attribute_value must be given together")
    if group and not any(g.lower() == group.lower() for g in ATTRIBUTE_GROUPS):
        raise ToolInputError(f"attribute_group must be one of: {', '.join(ATTRIBUTE_GROUPS)}")
    if vals["sort"] not in SORTS:
        raise ToolInputError(f"sort must be one of: {', '.join(SORTS)}")
    lo, hi = vals["min_price"], vals["max_price"]
    if lo is not None and hi is not None and lo > hi:
        raise ToolInputError("min_price is greater than max_price")
    vals["unsupported"] = sorted({UNSUPPORTED[k] for k in UNSUPPORTED if args.get(k) not in (None, "")} )
    return vals


def _stem(word: str) -> str:
    """'beds' -> 'bed', 'sofas' -> 'sofa', 'mattresses' -> 'mattress': product names are mostly singular."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _brand_from_keyword(args: dict[str, Any], brands: list[dict[str, Any]]) -> dict[str, Any]:
    """The model often sends a brand as a text keyword ("acme", "coaster"). A keyword only matches words in product
    NAMES, so the search finds nothing and the customer is told the store does not carry the brand. If the keyword is
    exactly a brand the store carries and no brand_slug was given, search by that brand instead."""
    kw = args.get("keyword")
    if not isinstance(kw, str) or args.get("brand_slug") or not _squash(kw) or len(kw.split()) > 4:
        return args
    want = _squash(kw)
    for b in brands:
        slug = b.get("slug")
        if slug and want in {_squash(str(slug)), _squash(str(b.get("name") or ""))}:
            return {**{k: v for k, v in args.items() if k != "keyword"}, "brand_slug": slug}
    return args


def _unknown_category(slug: str, client: ApiClient) -> dict[str, Any]:
    """An error the model can act on, with the closest real categories (best effort)."""
    close: list[dict[str, str]] = []
    try:
        words = [w for w in re.split(r"[-\s]+", slug) if w]
        stem = _stem(words[-1]) if words else slug
        rows = rows_from_tree(client.categories())
        close = [{"slug": r["slug"], "title": r["title"]} for r in rows
                 if stem and (stem in str(r["slug"]).lower() or stem in str(r["title"]).lower())]
        close = sorted(close, key=lambda r: len(r["slug"]))[:5]
    except ApiError:
        pass
    return {
        "error": "unknown_category",
        "note": (f"No category has the slug '{slug}'. Never guess a category_slug: call list_categories with a keyword "
                 "(for example 'sofa') to get a real slug, then search again with the same price limit."),
        "did_you_mean": close,
    }


def _passes_keyword(name: str, words: list[str]) -> bool:
    low = (name or "").lower()
    return all(_stem(w) in low for w in words)


def _is_component(name: str) -> bool:
    return any(p.search(name or "") for p in COMPONENT_NAME_PATTERNS)


def _fetch(client: ApiClient, v: dict[str, Any], query: str | None, need_all: bool, base_ok, price_ok) -> dict[str, Any]:
    """Page through the API. Returns the items that pass every filter ("kept"), the ones that pass every filter except
    price ("base_kept"), how many products were checked, the API's total and whether every page was read. Stops early
    once enough matches exist, unless the whole list is needed."""
    kept: list[dict[str, Any]] = []
    base_kept: list[dict[str, Any]] = []
    varies = 0   # matching products whose price depends on an option (size...), so no single price to compare
    checked, total, pages, last_page = 0, None, 0, None
    while pages < MAX_PAGES:
        pages += 1
        if v["category_slug"]:
            res = client.category_products(v["category_slug"], page=pages)
        else:
            res = client.search_products(query, page=pages)
        total, last_page = res.get("total"), res.get("last_page") or 1
        checked += len(res["products"])
        for p in res["products"]:
            if base_ok(p):
                base_kept.append(p)
                if p.get("price_varies") and p.get("price") is None:
                    varies += 1
                if price_ok(p):
                    kept.append(p)
        if pages >= last_page or (not need_all and len(kept) >= MAX_RESULTS):
            break
    complete = last_page is not None and pages >= last_page
    return {"kept": kept, "base_kept": base_kept, "varies": varies, "checked": checked, "total": total, "complete": complete}


def search_products(args: dict[str, Any], client: ApiClient | None = None, ctx=None) -> dict[str, Any]:
    """Tool entry point. Returns a JSON-friendly dict for the model."""
    try:
        v = _prepare(args)
    except ToolInputError as exc:
        return {"error": str(exc)}
    if v["unsupported"]:
        what = " and ".join(v["unsupported"])
        return {"error": "unsupported_filter",
                "note": (f"The store's search cannot filter by {what} right now. Search again without it, and tell the "
                         f"customer the results are not filtered by {what}. Never say a product matches {what} unless its "
                         "name says so.")}

    try:
        client = client or get_client(ctx)
        # The brand list is only needed to turn a brand slug into a name, or to spot a brand typed as a keyword.
        brands = client.brands() if (args.get("brand_slug") or args.get("keyword")) else []
        args = _brand_from_keyword(args, brands)
        brand_slug = _text(args, "brand_slug")
        keyword = _text(args, "keyword")
        brand_name = None
        if brand_slug:
            brand_name = next((b["name"] for b in brands if b.get("slug") == brand_slug and b.get("name")), None)
            if brand_name is None:
                return {"error": "unknown_brand",
                        "note": f"No brand has the slug '{brand_slug}'. Call list_brands to get a real slug."}

        query = keyword or brand_name
        if not v["category_slug"] and not query:
            return {"error": "give at least a keyword, a category_slug or a brand_slug"}

        lo, hi, sort = v["min_price"], v["max_price"], v["sort"]
        words = keyword.split()[:5] if (keyword and v["category_slug"]) else []   # a category listing has no keyword filter
        hide_parts = HIDE_COMPONENT_SKUS and v["category_slug"] != COMPONENT_CATEGORY
        wanted_brand = _squash(brand_name) if brand_name else None
        price_filtered = lo is not None or hi is not None

        def base_filter(p: dict[str, Any]) -> bool:        # everything except price
            if hide_parts and _is_component(p.get("name") or ""):
                return False
            if words and not _passes_keyword(p.get("name") or "", words):
                return False
            if wanted_brand and _squash(p.get("brand") or "") != wanted_brand:
                return False
            return True

        def price_ok(p: dict[str, Any]) -> bool:
            price = p.get("price")
            if price is None:
                return not price_filtered and sort == "name"
            return (lo is None or price >= lo) and (hi is None or price <= hi)

        need_all = sort in ("price_asc", "price_desc")
        fetched = _fetch(client, v, query, need_all, base_filter, price_ok)
        window = None
        if need_all and not fetched["complete"]:
            return {
                "error": "too_many_matches",
                "note": (f"This search matches about {fetched['total']} products, too many to sort by price exactly. Do not "
                         "name a cheapest or most expensive one. Search again with a keyword for the product type together "
                         "with the most specific category_slug, so the list is short enough."),
            }
        if not fetched["complete"]:
            window = {"checked": fetched["checked"], "of": fetched["total"]}
        rows = fetched["kept"]
    except ApiError as exc:
        if exc.status == 404 and v["category_slug"]:
            try:
                return _unknown_category(v["category_slug"], client)  # type: ignore[arg-type]
            except Exception:
                pass
        return {"error": "Product search is not available right now. Do not guess products or prices; offer the store's contact details."}

    if sort == "price_asc":
        rows = sorted(rows, key=lambda p: p["price"])
    elif sort == "price_desc":
        rows = sorted(rows, key=lambda p: -p["price"])
    shown = rows[:MAX_RESULTS]
    products = [{"sku": p.get("sku"), "slug": p.get("slug"), "name": p.get("name"), "price": p.get("price"),
                 "brand_name": p.get("brand"), "main_image": p.get("image_url"),
                 **({"price_varies": True} if p.get("price_varies") else {})} for p in shown]

    result: dict[str, Any] = {"count": len(products), "products": products}
    applied = {k: args[k] for k in ("min_price", "max_price") if args.get(k) is not None}
    if applied:
        result["applied_filters"] = applied                  # the price limits actually used, so the reply may state them
    result["sorted_by"] = "store order (not by name or price)" if sort == "name" else sort
    if window:
        result["searched"] = window
        result["partial_note"] = (f"Only the first {window['checked']} of {window['of']} matching products were checked. "
                                  "Do NOT say these are all the matches, and do not say there are none beyond them.")
    if any(p.get("price_varies") for p in products):
        result["price_note"] = ("A product with price_varies true has a price that depends on its option (for example the size); "
                                "its price is null here. Do not state a price for it. Call get_product_details with its slug to "
                                "see the price of each option.")
    elif any(p["price"] is None for p in products):
        result["price_note"] = "A product with price null has no price available. Do not state or guess its price."
    if fetched["varies"] and (price_filtered or need_all):
        result["price_varies_skipped"] = fetched["varies"]
        result["price_varies_note"] = (f"{fetched['varies']} matching product(s) (for example mattresses) have prices that depend on the "
                                       "size or option, so they could not be checked against the price limit or sorted by price. "
                                       "Say so, and do not claim the list includes every product in that price range.")
    if sort in ("price_asc", "price_desc") and not keyword and products:
        result["type_check"] = ("Parts and accessories can be filed in a category (drawer sets, board sets). Before naming the first "
                                "result as the cheapest or priciest of a type, check that its name says that type; if it does not, search "
                                "again with keyword set to the type word.")
    if len(rows) > MAX_RESULTS:
        result["note"] = (f"Showing only the first {MAX_RESULTS} matches; there are more, so do NOT say these are all of them; "
                          "suggest narrowing the filters. ")
        if sort == "name":
            result["note"] += ("This list is NOT sorted by price, so never call any of these the cheapest or priciest. "
                               "For 'cheapest' / 'most expensive' search again with the same filters and sort=price_asc / price_desc.")
        else:
            result["note"] += ("These are the cheapest matches, so the first one is the cheapest." if sort == "price_asc"
                               else "These are the most expensive matches, so the first one is the most expensive.")
    if not products:
        result["note"] = "No products matched. Suggest loosening a filter (price or keyword)."
        if window:
            result["note"] = ("None of the products checked matched, but not all matches were checked. Do NOT say the store has none; "
                              "suggest a more specific keyword or category.")
        if args.get("brand_slug") and v["category_slug"]:
            result["note"] += (" The category may simply be too narrow: before saying the store does not carry this brand, "
                               "search again with only brand_slug (and the same price limit).")
        if v["category_slug"] and not keyword:
            result["note"] += (" The category may hold only parts, or the items may be filed under another category: before saying we have "
                               "none, search again with keyword set to the product word (for example stool or armoire), the same price "
                               "limit, and no category_slug.")
        if keyword and not v["category_slug"]:
            result["note"] += (" Before saying we have none, look up the type with list_categories and search its category_slug "
                               "(with the same price limit); a keyword only matches words in product names.")
        if hi is not None and fetched["complete"]:
            near = sorted((p for p in fetched["base_kept"] if p.get("price") is not None and p["price"] > hi),
                          key=lambda p: p["price"])[:3]
            if near:
                result["cheapest_over_limit"] = [{"sku": p.get("sku"), "slug": p.get("slug"), "name": p.get("name"),
                                                  "price": p.get("price"), "brand_name": p.get("brand"),
                                                  "main_image": p.get("image_url")} for p in near]
                result["note"] = ("Nothing matched at or under that price. Say so, then in the SAME reply list the products in "
                                  "cheapest_over_limit (name and real price) as the closest options; do not just ask whether the "
                                  "customer wants to see them. Never invent a new price limit.")
    return result

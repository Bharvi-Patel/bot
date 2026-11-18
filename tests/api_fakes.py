"""Test helpers for tools that read the store API."""
from __future__ import annotations


class TreeClient:
    """A fake API client whose categories() returns a nested tree built from flat rows
    (category_id, parent_id, slug, title), the way GET /categories/tree nests them."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def categories(self):
        kids: dict = {}
        for r in self.rows:
            kids.setdefault(r["parent_id"] or 0, []).append(r)

        def node(r):
            return {"category_id": r["category_id"], "slug": r["slug"], "name": r["title"],
                    "children": [node(c) for c in kids.get(r["category_id"], [])]}

        return [node(r) for r in kids.get(0, [])]


class CatalogClient:
    """A fake API client for search_products. `products` are listing items (sku, slug, name, price, brand, image_url).
    search_products(q) returns the products whose name contains every word of q, 16 per page. category_products(slug)
    returns the products listed under that slug (a dict), or raises a 404 ApiError for an unknown slug."""

    PER_PAGE = 16

    def __init__(self, products=None, by_category=None, brands=None, tree=None, attributes=None):
        self.products = list(products or [])
        self.by_category = by_category or {}
        self._brands = brands if brands is not None else []
        self._tree = tree or []
        self._attributes = attributes or []   # facet groups: [{"group": "Bed Size", "values": [{"id": "u1", "name": "Queen"}]}]
        self.calls = []
        self.filters = []   # the API filters each search/category call was given, in call order

    def attribute_options(self, query):
        self.calls.append(("attributes", query))
        return [dict(g) for g in self._attributes]

    def brands(self):
        self.calls.append(("brands",))
        return self._brands

    def categories(self):
        self.calls.append(("categories",))
        return self._tree

    def _page(self, items, page):
        last = max(1, -(-len(items) // self.PER_PAGE))
        chunk = items[(page - 1) * self.PER_PAGE: page * self.PER_PAGE]
        return {"page": page, "last_page": last, "per_page": self.PER_PAGE, "total": len(items), "products": chunk}

    def search_products(self, query, page=1, **filters):
        self.calls.append(("search", query, page))
        self.filters.append(filters)
        words = query.lower().split()
        hits = [p for p in self.products if all(w in p["name"].lower() or w in (p.get("brand") or "").lower() for w in words)]
        return self._page(hits, page)

    def category_products(self, slug, page=1, **filters):
        self.calls.append(("category", slug, page))
        self.filters.append(filters)
        from bot.api_client import ApiError
        if slug not in self.by_category:
            raise ApiError("No query results", status=404)
        return self._page(self.by_category[slug], page)


def item(sku, name, price, brand="Acme", slug=None):
    return {"sku": sku, "slug": slug or sku.lower(), "name": name, "price": price, "brand": brand, "image_url": None, "tags": ""}

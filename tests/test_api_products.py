import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "rag"))
import api_products as ap

TREE = [{"category_id": 1, "slug": "mattresses-and-bedding", "name": "Mattresses & Bedding", "children": [
    {"category_id": 2, "slug": "mattresses-by-size", "name": "By Size", "children": []}]},
    {"category_id": 3, "slug": "desks", "name": "Desks", "children": []}]


class Fake:
    def __init__(self):
        self.listing = {"mattresses-and-bedding": ["m1"], "mattresses-by-size": ["m1", "m2"], "desks": ["d1"]}
        self.details = {
            "m1": dict(slug="m1", sku=None, name="Cloud Mattress", brand="Nectar", price=-0.01, price_varies=True,
                       short_description="Firm", description="<p>Soft</p>", special_features=None, dimensions={}, lifestyle=None,
                       categories=[{"slug": "mattresses-by-size", "name": "By Size"}],
                       options=[{"sku": "M1-Q", "slug": "m1-q", "price": 599.0, "attributes": {"Size": "Queen"}},
                                {"sku": "M1-K", "slug": "m1-k", "price": 799.0, "attributes": {"Size": "King"}}]),
            "m2": dict(slug="m2", sku="M2", name="Broken", brand=None, price=-0.01, price_varies=True, categories=[], options=[]),
            "d1": dict(slug="d1", sku="D1", name="Oak Desk", brand="Acme Co", price=250.0, price_varies=False,
                       dimensions={"width": 48}, categories=[{"slug": "desks", "name": "Desks"}], options=[]),
        }

    def categories(self):
        return TREE

    def brands(self):
        return [{"slug": "acme", "name": "Acme Co"}]

    def category_products(self, slug, page=1):
        return {"products": [{"slug": s} for s in self.listing[slug]], "last_page": 1, "total": len(self.listing[slug])}

    def product_detail(self, slug):
        return self.details[slug]


def test_scope_includes_subcategories_and_reports_unknown():
    chosen, missing, scope = ap.scope_from_tree(TREE, ["mattresses-and-bedding", "nope"], [])
    assert chosen == ["mattresses-and-bedding"] and missing == ["nope"]
    assert set(scope) == {"mattresses-and-bedding", "mattresses-by-size"}


def test_ingest_indexes_option_priced_products_and_reports_skips():
    skipped, notes = [], []
    rows = ap.ingest(Fake(), ["mattresses-and-bedding", "desks", "ghost"], skipped=skipped, notes=notes)
    by = {r["source_key"]: r for r in rows}
    assert set(by) == {"m1", "d1"}
    assert by["m1"]["price"] == 599.0 and "Queen: $599.00" in by["m1"]["content"] and "King: $799.00" in by["m1"]["content"]
    assert by["m1"]["category_slugs"] == ["mattresses-and-bedding", "mattresses-by-size"]   # ancestors included
    assert by["m1"]["brand_slug"] == "nectar" and "SKU: M1-Q, M1-K" in by["m1"]["content"]   # fallback slug; option SKUs
    assert by["d1"]["brand_slug"] == "acme" and by["d1"]["price"] == 250.0
    assert [s for s, _ in skipped] == ["m2"] and any("ghost" in n for n in notes)
    assert any("category desks: API total 1, collected 1" in n for n in notes)


def test_limit_stops_early():
    assert len(ap.listing_slugs(Fake(), ["mattresses-by-size", "desks"], limit=1)) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

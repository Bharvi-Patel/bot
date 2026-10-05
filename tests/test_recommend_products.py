from sjbot.product_docs import compose_document, descendants, slugs_with_ancestors
from sjbot.tools.recommend_products import recommend_products


def doc(i, sim, sku=None, price=500.0):
    sku = sku or f"SKU{i}"
    return {"id": i, "source_key": f"u{i}", "title": f"Sofa {i}", "url_path": f"/product/s{i}", "brand_slug": "b",
            "price": price, "content": f"Sofa {i}\nBrand: B | Categories: Sofas | SKU: {sku}", "similarity": sim}


def runner(rows, seen=None):
    def run(sql, params):
        if seen is not None: seen.append((sql, params))
        return rows if "ORDER BY embedding" in sql else []
    return run


def live(prices, missing=()):
    def confirm(a):
        if a["sku"] in missing: return {"found": False}
        return {"found": True, "product": {"name": f"Live {a['sku']}", "price": prices[a["sku"]]}, "attributes": {"Color": ["Gray"]}}
    return confirm


emb = lambda q: [0.1, 0.2]


def test_price_comes_from_live_sql_not_the_index():
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8, price=999.0)]), embed_query=emb, confirm=live({"SKU1": 612.5}))
    assert out["products"][0]["price"] == 612.5 and out["products"][0]["name"] == "Live SKU1"


def test_live_price_over_budget_is_dropped_even_if_index_said_ok():
    out = recommend_products({"query": "sofa", "max_price": 800}, run_query=runner([doc(1, 0.8, price=700.0), doc(2, 0.7)]),
                             embed_query=emb, confirm=live({"SKU1": 950.0, "SKU2": 750.0}))
    assert [p["sku"] for p in out["products"]] == ["SKU2"] and out["dropped_by_live_check"] == 1


def test_product_gone_from_sql_is_dropped():
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8), doc(2, 0.7)]), embed_query=emb,
                             confirm=live({"SKU2": 400.0}, missing={"SKU1"}))
    assert [p["sku"] for p in out["products"]] == ["SKU2"]


def test_filters_are_pushed_into_the_query_and_limit_applies():
    seen = []
    rows = [doc(i, 0.9 - i / 100) for i in range(8)]
    out = recommend_products({"query": "desk", "max_price": 500, "brand_slug": "b", "category_slug": "desks", "limit": 3},
                             run_query=runner(rows, seen), embed_query=emb, confirm=live({f"SKU{i}": 100.0 for i in range(8)}))
    sql, params = seen[0]
    assert "price <= %s" in sql and "brand_slug = %s" in sql and "= ANY(category_slugs)" in sql
    assert params[1:4] == [500, "b", "desks"] and len(out["products"]) == 3


def test_under_cutoff_means_not_found():
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.3)]), embed_query=emb, confirm=live({}))
    assert out["found"] is False and out["best_similarity"] == 0.3 and "Do not invent" in out["note"]


def test_bad_args_and_db_failure():
    assert "error" in recommend_products({}, embed_query=emb)
    assert "error" in recommend_products({"query": "x", "max_price": "800"}, embed_query=emb)
    assert "error" in recommend_products({"query": "x", "sql": "1"}, embed_query=emb)
    def boom(sql, params): raise RuntimeError("secret host")
    out = recommend_products({"query": "x"}, run_query=boom, embed_query=emb, confirm=live({}))
    assert out["error"] == "product search is unavailable right now" and "secret" not in str(out)


def test_document_template_skips_empty_lines_and_never_has_price():
    p = {"name": "Adlai Sofa", "sku": "A1", "brand_name": "Acme", "price": 499, "lifestyle": "Modern", "width": 72, "depth": 0,
         "height": None, "short_description": "<p>Compact.</p>", "full_description": "", "special_features": None}
    text = compose_document(p, ["Sofas", "Sofas", "Living"], {"Color": ["Gray", "Gray", "Blue"], "Material": ["Fabric"]})
    assert text.splitlines() == ["Adlai Sofa", "Brand: Acme | Categories: Sofas | Living | SKU: A1", "Style: Modern",
                                 "Attributes: Color: Gray, Blue; Material: Fabric", "Dimensions (in): 72 W", "Summary: Compact."]
    assert "499" not in text


def test_category_tree_helpers():
    cats = [dict(category_id=1, category_uuid="a", parent_id=None, slug="seating", title="Seating"),
            dict(category_id=2, category_uuid="b", parent_id=1, slug="sofas", title="Sofas"),
            dict(category_id=3, category_uuid="c", parent_id=2, slug="sectionals", title="Sectionals"),
            dict(category_id=4, category_uuid="d", parent_id=None, slug="beds", title="Beds")]
    scope, missing = descendants(cats, ["sofas", "nope"])
    assert sorted(c["slug"] for c in scope) == ["sectionals", "sofas"] and missing == ["nope"]
    assert slugs_with_ancestors("c", {c["category_uuid"]: c for c in cats}, {c["category_id"]: c for c in cats}) == ["sectionals", "sofas", "seating"]

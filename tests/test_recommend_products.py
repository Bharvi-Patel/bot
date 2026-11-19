import pytest

from bot.product_docs import compose_document
from bot.tools.recommend_products import recommend_products


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


@pytest.fixture(autouse=True)
def _single_store(monkeypatch):
    monkeypatch.setenv("BOT_STORE_UUID", "store-1")   # tools find the store from ctx, or this setting for local use


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
    assert params[1:5] == ["store-1", 500, "b", "desks"] and len(out["products"]) == 3


def test_the_search_is_limited_to_the_chats_own_store_never_another():
    from types import SimpleNamespace
    seen = []
    recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8)], seen), embed_query=emb, confirm=live({"SKU1": 100.0}),
                       ctx=SimpleNamespace(store_uuid="store-b"))
    assert all("store_uuid = %s" in sql and params[1] == "store-b" for sql, params in seen) and seen


def test_the_live_check_is_made_for_the_same_store(monkeypatch):
    from types import SimpleNamespace
    import bot.tools.get_product_details as gpd
    asked = []
    monkeypatch.setattr(gpd, "get_product_details", lambda a, ctx=None, **k: asked.append((a, getattr(ctx, "store_uuid", None))) or {"found": False})
    recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8)]), embed_query=emb, ctx=SimpleNamespace(store_uuid="store-b"))
    # the index row's page is /product/s1, so the slug is tried first; it found nothing, so the SKU is tried next
    assert asked == [({"slug": "s1"}, "store-b"), ({"sku": "SKU1"}, "store-b")]


def test_no_store_means_no_recommendations(monkeypatch):
    monkeypatch.delenv("BOT_STORE_UUID", raising=False)
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8)]), embed_query=emb, confirm=live({"SKU1": 1.0}))
    assert "error" in out and "products" not in out


def test_a_product_priced_by_size_is_kept_with_its_options_and_checked_against_the_budget():
    def confirm(a):
        return {"found": True, "product": {"name": "Royal Mattress", "price": None}, "price_range": {"min": 339.99, "max": 659.99},
                "options": [{"price": 339.99, "attributes": {"Bed Size": "Twin"}}, {"price": 659.99, "attributes": {"Bed Size": "King"}}]}
    ok = recommend_products({"query": "mattress", "max_price": 400}, run_query=runner([doc(1, 0.8)]), embed_query=emb, confirm=confirm)
    assert ok["products"][0]["price"] is None and ok["products"][0]["price_range"] == {"min": 339.99, "max": 659.99}
    assert len(ok["products"][0]["options"]) == 2 and "price null is priced by option" in ok["note"]
    too_cheap_budget = recommend_products({"query": "mattress", "max_price": 100}, run_query=runner([doc(1, 0.8)]), embed_query=emb, confirm=confirm)
    assert too_cheap_budget["found"] is False and too_cheap_budget["dropped_by_live_check"] == 1


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


def test_live_check_uses_one_api_call_per_product_and_no_category_tree(monkeypatch):
    import bot.tools.get_product_details as gpd
    calls = []
    monkeypatch.setattr(gpd, "get_product_details", lambda a, ctx=None, with_categories=True, **k: calls.append((a, with_categories)) or
                        {"found": True, "product": {"name": "Sofa 1", "price": 100.0, "sku": "SKU1"}})
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8)]), embed_query=emb)
    assert out["count"] == 1 and calls == [({"slug": "s1"}, False)]


def test_a_slug_that_belongs_to_another_product_is_not_trusted(monkeypatch):
    import bot.tools.get_product_details as gpd
    answers = {"s1": {"found": True, "product": {"name": "Other Sofa", "price": 100.0, "sku": "OTHER"}},
               "SKU1": {"found": True, "product": {"name": "Sofa 1", "price": 120.0, "sku": "SKU1"}}}
    monkeypatch.setattr(gpd, "get_product_details", lambda a, ctx=None, **k: answers[a.get("slug") or a.get("sku")])
    out = recommend_products({"query": "sofa"}, run_query=runner([doc(1, 0.8)]), embed_query=emb)
    assert out["products"][0]["name"] == "Sofa 1" and out["products"][0]["price"] == 120.0


def test_a_page_that_is_not_a_product_route_goes_straight_to_the_sku(monkeypatch):
    import bot.tools.get_product_details as gpd
    asked = []
    row = {**doc(1, 0.8), "url_path": "/shop/sofa-1?ref=x"}
    monkeypatch.setattr(gpd, "get_product_details", lambda a, ctx=None, **k: asked.append(a) or {"found": False})
    recommend_products({"query": "sofa"}, run_query=runner([row]), embed_query=emb)
    assert asked == [{"sku": "SKU1"}]

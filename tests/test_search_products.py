import pytest

from sjbot.tools.search_products import (
    MAX_RESULTS, ToolInputError, build_search_query, search_products,
)


def test_no_filters_returns_capped_list_one_row_per_family():
    sql, params = build_search_query({})
    assert f"LIMIT {MAX_RESULTS}" in sql
    assert "ROW_NUMBER() OVER (PARTITION BY" in sql
    assert "WHERE rn = 1" in sql
    assert "WHERE p." not in sql          # no filters, so no filter clause
    assert params == []


def test_result_includes_matching_options_count():
    sql, _ = build_search_query({})
    assert "matching_options" in sql


def test_variants_are_searched_not_excluded():
    sql, _ = build_search_query({"color": "Black"})
    assert "parent_uuid IS NULL" not in sql


def test_include_variants_is_no_longer_a_parameter():
    with pytest.raises(ToolInputError):
        build_search_query({"include_variants": True})


def test_category_matches_product_or_its_parent():
    sql, _ = build_search_query({"category_slug": "sofas-and-seating-sofas"})
    assert "cat_products AS" in sql
    assert "p.product_uuid IN (SELECT product_uuid FROM cat_products)" in sql
    assert "p.parent_uuid IN (SELECT product_uuid FROM cat_products)" in sql


def test_search_reads_slim_view_not_the_wide_one():
    sql, _ = build_search_query({})
    assert "vw_chat_product_search" in sql
    assert "vw_chat_products" not in sql


def test_price_range_uses_placeholders_not_values():
    sql, params = build_search_query({"min_price": 300, "max_price": 1200})
    assert "p.price >= %s" in sql and "p.price <= %s" in sql
    assert "300" not in sql and "1200" not in sql
    assert params[:2] == [300.0, 1200.0]


def test_category_adds_recursive_subtree_and_comes_first_in_params():
    sql, params = build_search_query({"category_slug": "sofas-and-seating-sofas", "max_price": 1000})
    assert sql.startswith("WITH RECURSIVE sub")
    assert params[0] == "sofas-and-seating-sofas"
    assert params[1] == 1000.0


def test_keyword_words_are_and_ed_and_wildcards_escaped():
    sql, params = build_search_query({"keyword": "black 100%_sofa"})
    assert sql.count("CONCAT_WS(' ', p.name, par.name) LIKE %s") == 2
    assert "%black%" in params
    assert "%100\\%\\_sofa%" in params


def test_sql_injection_text_stays_in_params():
    evil = "x'; DROP TABLE products; --"
    sql, params = build_search_query({"brand_slug": evil})
    assert "DROP TABLE" not in sql
    assert evil in params


def test_color_filter_uses_attribute_view():
    sql, params = build_search_query({"color": "Black"})
    assert "vw_chat_product_attributes" in sql
    assert "Black" in params


def test_width_filter():
    sql, params = build_search_query({"max_width_in": 80})
    assert "p.width <= %s" in sql and 80.0 in params


@pytest.mark.parametrize("sort,fragment", [
    ("price_asc", "ORDER BY price ASC"), ("price_desc", "ORDER BY price DESC"), ("name", "ORDER BY name ASC"),
])
def test_sort_whitelist(sort, fragment):
    sql, _ = build_search_query({"sort": sort})
    assert fragment in sql


def test_unknown_sort_rejected():
    with pytest.raises(ToolInputError):
        build_search_query({"sort": "price; DROP TABLE products"})


@pytest.mark.parametrize("bad", [
    {"max_price": -5},
    {"max_price": "cheap"},
    {"min_price": 500, "max_price": 100},
    {"keyword": "x" * 500},
    {"colour": "Black"},          # misspelled key from the model
    {"max_price": True},
])
def test_bad_input_rejected(bad):
    with pytest.raises(ToolInputError):
        build_search_query(bad)


def test_tool_returns_error_dict_not_exception():
    out = search_products({"max_price": -1}, run_query=lambda *_: [])
    assert "error" in out


def test_tool_no_results_note():
    out = search_products({"max_price": 10}, run_query=lambda *_: [])
    assert out["count"] == 0 and "loosening" in out["note"]


def test_tool_full_page_note():
    rows = [{"sku": str(i)} for i in range(MAX_RESULTS)]
    out = search_products({}, run_query=lambda *_: rows)
    assert out["count"] == MAX_RESULTS and "narrowing" in out["note"]


def test_search_results_get_full_image_urls(monkeypatch):
    monkeypatch.setenv("SJ_IMAGE_BASE_URL", "https://shop.example/media")
    out = search_products({}, run_query=lambda *_: [{"sku": "A", "main_image": "a.webp"}])
    assert out["products"][0]["main_image"] == "https://shop.example/media/a.webp"


# ---- size categories: the category sits on the parent family, each variant records its own "Bed Size" ----
import sqlite3 as _sqlite3


def _size_db():
    db = _sqlite3.connect(":memory:")
    db.row_factory = _sqlite3.Row
    db.executescript("""
    CREATE TABLE vw_chat_product_search(product_uuid,parent_uuid,sku,slug,name,price,brand_name,brand_slug,width,height,depth,main_image);
    CREATE TABLE vw_chat_categories(category_id,category_uuid,parent_id,slug,title);
    CREATE TABLE vw_chat_product_categories(product_uuid,category_uuid);
    CREATE TABLE vw_chat_product_attributes(product_uuid,group_name,value);
    INSERT INTO vw_chat_categories VALUES (1,'c-mat',NULL,'mattresses-and-bedding','Mattresses and Bedding'),
      (2,'c-size',1,'mattresses-by-size','Mattresses by Size'),(3,'c-queen',2,'mattresses-by-size-queen','Queen'),
      (4,'c-twin',2,'mattresses-by-size-twin','Twin');
    """)

    def prod(u, par, sku, name, price):
        db.execute("INSERT INTO vw_chat_product_search VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (u, par, sku, sku.lower(), name, price, "B", "b", None, None, None, None))
    for u, sku, n, pr, sz in [("t", "M1", "Chime Twin Mattress", 154.95, "Twin"), ("f", "M2", "Chime Full Mattress", 193.17, "Full"),
                              ("q", "M3", "Chime Queen Mattress", 220.54, "Queen"), ("k", "M4", "Chime King Mattress", 280.45, "King")]:
        prod(u, "chime", sku, n, pr)
        db.execute("INSERT INTO vw_chat_product_attributes VALUES (?,?,?)", (u, "Bed Size", sz))
    for c in ("c-queen", "c-twin"):
        db.execute("INSERT INTO vw_chat_product_categories VALUES ('chime',?)", (c,))      # the parent family, not a row itself
    prod("art", None, "DM510-Q", "Artemisia", 239.80)                                      # standalone, linked straight to Queen
    db.execute("INSERT INTO vw_chat_product_categories VALUES ('art','c-queen')")
    prod("ns", "nosize", "NS-A", "No Size Mattress", 310)                                  # variant with no size on file
    db.execute("INSERT INTO vw_chat_product_categories VALUES ('nosize','c-queen')")
    return db


def _names(db, args):
    sql, params = build_search_query({"sort": "price_asc", **args})
    return [(r["name"], r["price"]) for r in db.execute(sql.replace("%s", "?").replace("%%", "%"), params)]


@pytest.mark.skipif(_sqlite3.sqlite_version_info < (3, 44), reason="needs CONCAT_WS")
def test_size_category_returns_only_that_sizes_variant_price():
    db = _size_db()
    assert _names(db, {"category_slug": "mattresses-by-size-queen"})[0] == ("Chime Queen Mattress", 220.54)    # not the $154.95 Twin
    assert _names(db, {"category_slug": "mattresses-by-size-twin"}) == [("Chime Twin Mattress", 154.95)]
    assert _names(db, {"category_slug": "mattresses-by-size-queen", "max_price": 230}) == [("Chime Queen Mattress", 220.54)]


@pytest.mark.skipif(_sqlite3.sqlite_version_info < (3, 44), reason="needs CONCAT_WS")
def test_size_filter_keeps_standalone_products_and_variants_without_a_size():
    names = [n for n, _ in _names(_size_db(), {"category_slug": "mattresses-by-size-queen"})]
    assert "Artemisia" in names and "No Size Mattress" in names


@pytest.mark.skipif(_sqlite3.sqlite_version_info < (3, 44), reason="needs CONCAT_WS")
def test_broad_category_applies_no_size_filter():
    db = _size_db()
    sql, params = build_search_query({"category_slug": "mattresses-and-bedding"})
    row = [r for r in db.execute(sql.replace("%s", "?").replace("%%", "%"), params) if r["name"].startswith("Chime")][0]
    assert row["matching_options"] == 4            # Twin, Full, Queen and King all still match
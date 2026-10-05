import pytest

from sjbot.text import clean_text
from sjbot.tools.get_product_details import _category_paths, get_product_details


# ---------- text cleaning ----------
def test_clean_text_strips_html_and_entities():
    raw = "<p>Soft &amp; <b>deep</b> seat</p><ul><li>Reversible</li><li>Pet friendly</li></ul>"
    out = clean_text(raw, 500)
    assert "<" not in out and "&amp;" not in out
    assert "Soft & deep seat" in out
    assert "- Reversible" in out and "- Pet friendly" in out


def test_clean_text_drops_scripts_and_collapses_space():
    assert clean_text("a<script>alert(1)</script>   b", 50) == "a b"


def test_clean_text_truncates_on_word_boundary():
    out = clean_text("word " * 100, 50)
    assert len(out) <= 53 and out.endswith("...")


@pytest.mark.parametrize("raw", [None, "", "   ", "<p></p>"])
def test_clean_text_empty_is_none(raw):
    assert clean_text(raw, 100) is None


# ---------- category paths ----------
TREE = [
    {"category_id": 1, "category_uuid": "c1", "parent_id": 0, "slug": "living", "title": "Living Room"},
    {"category_id": 2, "category_uuid": "c2", "parent_id": 1, "slug": "sofas", "title": "Sofas"},
    {"category_id": 3, "category_uuid": "c3", "parent_id": 2, "slug": "sect", "title": "Sectionals"},
    {"category_id": 4, "category_uuid": "c4", "parent_id": 0, "slug": "bed", "title": "Bedroom"},
]


def test_paths_keep_most_specific_and_drop_prefixes():
    paths = _category_paths(TREE, {"c1", "c2", "c3", "c4"})
    assert "Living Room > Sofas > Sectionals" in paths
    assert "Living Room > Sofas" not in paths and "Living Room" not in paths
    assert "Bedroom" in paths


def test_paths_survive_a_parent_loop():
    loop = [
        {"category_id": 1, "category_uuid": "a", "parent_id": 2, "slug": "a", "title": "A"},
        {"category_id": 2, "category_uuid": "b", "parent_id": 1, "slug": "b", "title": "B"},
    ]
    assert _category_paths(loop, {"a"})  # no infinite loop


# ---------- the tool, with a pretend database ----------
PRODUCT = {
    "product_uuid": "p-var", "sku": "S-BLK", "slug": "sofa-black", "name": "Sofa, Black",
    "short_description": "<p>Roomy sofa</p>", "full_description": "<p>Long &amp; deep.</p>",
    "special_features": "<ul><li>Washable</li></ul>", "price": 899.0, "brand_name": "Acme",
    "height": 35.0, "width": 84.0, "length": None, "depth": 38.0, "weight": 110.0, "pieces": 1,
    "lifestyle": "modern", "main_image": "img.jpg", "parent_uuid": "p-par",
}


def fake_db(product=PRODUCT, images=None):
    calls = []

    def run(sql, params=()):
        calls.append((sql, list(params)))
        if "FROM vw_chat_product_detail WHERE sku" in sql or "FROM vw_chat_product_detail WHERE slug" in sql:
            return [product] if product else []
        if "WHERE parent_uuid" in sql:
            return [
                {"product_uuid": "p-var", "sku": "S-BLK", "slug": "sofa-black", "name": "Sofa, Black", "price": 899.0, "main_image": "a"},
                {"product_uuid": "p-var", "sku": "S-BLK", "slug": "sofa-black", "name": "Sofa, Black", "price": 899.0, "main_image": "a"},  # duplicate English row
                {"product_uuid": "p-gry", "sku": "S-GRY", "slug": "sofa-grey", "name": "Sofa, Grey", "price": 949.0, "main_image": "b"},
            ]
        if "WHERE product_uuid = %s ORDER BY lang_row_id" in sql:
            return [{"sku": "S", "slug": "sofa", "name": "Sofa"}]
        if "FROM vw_chat_product_attributes" in sql:
            return [
                {"product_uuid": "p-var", "group_name": "Color", "value": "Black"},
                {"product_uuid": "p-gry", "group_name": "Color", "value": "Grey"},
                {"product_uuid": "p-par", "group_name": "Material", "value": "Fabric"},
            ]
        if "FROM vw_chat_product_categories" in sql:
            return [{"category_uuid": "c3"}]
        if "FROM vw_chat_categories" in sql:
            return TREE
        if "FROM vw_chat_product_images" in sql:
            return images if images is not None else [{"image_url": "u1", "image_alt": "front", "image_type": "main"}]
        raise AssertionError("unexpected query: " + sql)

    return run, calls


def test_found_product_shape_and_cleaning():
    run, _ = fake_db()
    out = get_product_details({"sku": "S-BLK"}, run_query=run)
    assert out["found"] is True
    p = out["product"]
    assert p["summary"] == "Roomy sofa" and p["description"] == "Long & deep."
    assert p["features"] == "- Washable"
    assert p["is_variant"] is True
    assert out["parent"]["name"] == "Sofa"
    assert out["categories"] == ["Living Room > Sofas > Sectionals"]
    assert out["attributes"] == {"Color": ["Black"]}


def test_options_are_deduped_and_carry_their_attributes():
    run, _ = fake_db()
    out = get_product_details({"sku": "S-BLK"}, run_query=run)
    assert out["option_count"] == 2
    by_sku = {o["sku"]: o for o in out["options"]}
    assert by_sku["S-GRY"]["attributes"] == {"Color": ["Grey"]}
    assert by_sku["S-BLK"]["is_this_product"] is True


def test_dimension_note_is_included():
    run, _ = fake_db()
    assert "unit" in get_product_details({"sku": "S-BLK"}, run_query=run)["product"]["dimension_note"]


def test_not_found():
    run, _ = fake_db(product=None)
    out = get_product_details({"sku": "NOPE"}, run_query=run)
    assert out["found"] is False and "search_products" in out["note"]


def test_variant_without_images_falls_back_to_parent_images():
    run, calls = fake_db(images=[])
    get_product_details({"sku": "S-BLK"}, run_query=run)
    image_calls = [c for c in calls if "vw_chat_product_images" in c[0]]
    assert len(image_calls) == 2 and image_calls[1][1][0] == "p-par"


def test_sku_preferred_over_slug_and_value_is_a_parameter():
    run, calls = fake_db()
    evil = "x' OR 1=1 --"
    get_product_details({"sku": evil, "slug": "ignored"}, run_query=run)
    sql, params = calls[0]
    assert "sku = %s" in sql and "slug = %s" not in sql
    assert evil not in sql and params == [evil]


@pytest.mark.parametrize("bad", [{}, {"sku": ""}, {"sku": 5}, {"sku": "x" * 500}, {"upc": "123"}])
def test_bad_input_returns_error_not_exception(bad):
    run, _ = fake_db()
    assert "error" in get_product_details(bad, run_query=run)


def test_all_null_dimensions_say_so():
    empty = dict(PRODUCT, width=None, height=None, depth=None, length=None, weight=None)
    run, _ = fake_db(product=empty)
    p = get_product_details({"sku": "S-BLK"}, run_query=run)["product"]
    assert p["dimensions"] is None
    assert "do not estimate" in p["dimension_note"]


def test_images_use_base_url(monkeypatch):
    monkeypatch.setenv("SJ_IMAGE_BASE_URL", "https://shop.example/media")
    run, _ = fake_db()
    out = get_product_details({"sku": "S-BLK"}, run_query=run)
    assert out["images"][0]["image_url"] == "https://shop.example/media/u1"
    assert out["product"]["main_image"] == "https://shop.example/media/img.jpg"

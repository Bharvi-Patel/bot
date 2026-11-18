import pytest

from bot.api_client import ApiError
from bot.tools.list_attribute_values import list_attribute_values
from tests.api_fakes import CatalogClient

BED = [{"group": "Bed Size", "values": [{"id": "u1", "name": "Queen"}, {"id": "u2", "name": "King"}, {"id": "u3", "name": "Twin"},
                                        {"id": "u4", "name": "Queen"}]}]


def test_values_come_from_the_facets_of_a_keyword_search():
    c = CatalogClient(attributes=BED)
    out = list_attribute_values({"group": "bed size", "keyword": " bed "}, client=c)
    assert c.calls == [("attributes", "bed")]
    assert out["found"] and out["group"] == "Bed Size"
    assert out["values"] == [{"value": "King"}, {"value": "Queen"}, {"value": "Twin"}]   # sorted, duplicates removed, no counts
    assert "not the whole store" in out["note"] and "never call the results every product" in out["note"]


def test_a_group_with_no_values_for_the_keyword_says_so():
    out = list_attribute_values({"group": "Color", "keyword": "bed"}, client=CatalogClient(attributes=BED))
    assert out["found"] is False and "cannot filter by Color" in out["note"] and "values" not in out


@pytest.mark.parametrize("bad", [
    {}, {"group": ""}, {"group": 5}, {"group": "Nope", "keyword": "bed"}, {"group": "Color", "keyword": "bed", "x": 1}])
def test_bad_input_is_refused_before_any_request(bad):
    c = CatalogClient(attributes=BED)
    assert "error" in list_attribute_values(bad, client=c) and c.calls == []


@pytest.mark.parametrize("kw", [None, "", " ", "a", 5])
def test_keyword_of_two_letters_or_more_is_required(kw):
    c = CatalogClient(attributes=BED)
    out = list_attribute_values({"group": "Color", **({} if kw is None else {"keyword": kw})}, client=c)
    assert out["error"] == "keyword_required" and c.calls == []


def test_api_failure_gives_a_safe_message():
    class Down:
        def attribute_options(self, q):
            raise ApiError("boom", status=500)
    out = list_attribute_values({"group": "Color", "keyword": "bed"}, client=Down())
    assert "not available right now" in out["error"] and "Do not guess" in out["error"]


def test_values_are_capped():
    many = [{"group": "Color", "values": [{"id": f"u{i}", "name": f"Shade {i:03d}"} for i in range(100)]}]
    out = list_attribute_values({"group": "Color", "keyword": "rug"}, client=CatalogClient(attributes=many))
    assert len(out["values"]) == 40

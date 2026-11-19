import json

import pytest

from bot.config import full_image_url
from bot.tools.get_shipping_options import get_shipping_options
from bot.tools.get_store_info import get_store_info
from bot.tools.list_categories import list_categories
from tests.api_fakes import TreeClient

TREE = [
    {"category_id": 1, "parent_id": 0, "slug": "living-room", "title": "Living Room"},
    {"category_id": 2, "parent_id": 1, "slug": "sofas-and-seating", "title": "Sofas and Seating"},
    {"category_id": 3, "parent_id": 2, "slug": "sofas-and-seating-sectional-sofas", "title": "Sectional Sofas"},
    {"category_id": 4, "parent_id": 2, "slug": "sofas-and-seating-sofas", "title": "Sofas"},
    {"category_id": 5, "parent_id": 0, "slug": "bedroom", "title": "Bedroom"},
    {"category_id": 6, "parent_id": None, "slug": "outdoor", "title": "Outdoor"},
]


tree_client = TreeClient(TREE)


# ---------- images ----------
def test_image_url_with_base(monkeypatch):
    monkeypatch.setenv("SJ_IMAGE_BASE_URL", "https://shop.example/media/")
    assert full_image_url("a.webp") == "https://shop.example/media/a.webp"
    assert full_image_url("/a.webp") == "https://shop.example/media/a.webp"


def test_image_url_without_base_returns_name(monkeypatch):
    monkeypatch.delenv("SJ_IMAGE_BASE_URL", raising=False)
    assert full_image_url("a.webp") == "a.webp"


def test_image_url_already_full_and_empty(monkeypatch):
    monkeypatch.setenv("SJ_IMAGE_BASE_URL", "https://x")
    assert full_image_url("https://cdn/y.jpg") == "https://cdn/y.jpg"
    assert full_image_url(None) is None and full_image_url("") is None


# ---------- list_categories ----------
def test_roots_when_no_arguments():
    out = list_categories({}, client=tree_client)
    assert [c["slug"] for c in out["categories"]] == ["bedroom", "living-room", "outdoor"]
    living = next(c for c in out["categories"] if c["slug"] == "living-room")
    assert living["has_subcategories"] is True
    assert next(c for c in out["categories"] if c["slug"] == "bedroom")["has_subcategories"] is False


def test_children_of_a_parent():
    out = list_categories({"parent_slug": "sofas-and-seating"}, client=tree_client)
    assert [c["title"] for c in out["categories"]] == ["Sectional Sofas", "Sofas"]
    assert out["parent"]["path"] == "Living Room > Sofas and Seating"


def test_unknown_parent():
    out = list_categories({"parent_slug": "nope"}, client=tree_client)
    assert out["found"] is False


def test_keyword_search_returns_paths_shallowest_first():
    out = list_categories({"keyword": "SOFA"}, client=tree_client)
    paths = [m["path"] for m in out["matches"]]
    assert "Living Room > Sofas and Seating > Sectional Sofas" in paths
    assert out["matches"][0]["title"] == "Sofas and Seating"


def test_keyword_no_hits_has_note():
    assert "No category" in list_categories({"keyword": "zzz"}, client=tree_client)["note"]


@pytest.mark.parametrize("bad", [{"parent_slug": "a", "keyword": "b"}, {"colour": "x"}, {"keyword": 5}, {"keyword": "x" * 300}])
def test_categories_bad_input(bad):
    assert "error" in list_categories(bad, client=tree_client)


# ---------- get_store_info ----------
from datetime import datetime, timezone

from bot.tools.get_store_info import format_hours, open_now

HOURS = {
    "friday": {"open": "09:00", "close": "19:00", "is_open": True},
    "monday": {"open": "09:00", "close": "19:00", "is_open": True},
    "sunday": {"open": "11:00", "close": "18:00", "is_open": True},
    "tuesday": {"open": "09:00", "close": "19:00", "is_open": True},
    "saturday": {"open": "09:00", "close": "19:00", "is_open": True},
    "thursday": {"open": "09:00", "close": "19:00", "is_open": True},
    "wednesday": {"open": "09:00", "close": "19:00", "is_open": True},
}



def test_hours_are_monday_first_in_12_hour_time():
    lines, summary = format_hours(HOURS)
    assert [l["day"] for l in lines][:2] == ["Monday", "Tuesday"] and lines[-1]["day"] == "Sunday"
    assert lines[0]["hours"] == "9:00 AM - 7:00 PM"
    assert lines[6]["hours"] == "11:00 AM - 6:00 PM"


def test_summary_merges_consecutive_days():
    _, summary = format_hours(HOURS)
    assert summary == "Monday to Saturday: 9:00 AM - 7:00 PM; Sunday: 11:00 AM - 6:00 PM"


def test_closed_and_missing_days():
    hours = dict(HOURS, tuesday={"open": "09:00", "close": "19:00", "is_open": False})
    del hours["wednesday"]
    lines, summary = format_hours(hours)
    assert lines[1]["hours"] == "Closed" and lines[2]["hours"] == "not listed"
    assert "Tuesday: Closed" in summary


def test_open_now_inside_and_outside_hours():
    # Thursday 2026-10-01 15:00 UTC = 10:00 AM in Chicago (CDT): open
    inside = open_now(HOURS, "America/Chicago", now=datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc))
    assert inside["is_open"] is True and "Thursday" in inside["local_time"]
    # 03:00 UTC Friday = 10:00 PM Thursday in Chicago: closed
    outside = open_now(HOURS, "America/Chicago", now=datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc))
    assert outside["is_open"] is False


def test_open_now_unknown_timezone_is_none():
    assert open_now(HOURS, "Not/AZone") is None
    assert open_now(HOURS, None) is None


API_INFO = {
    "name": "Shop", "phone": "555-1234", "email": "hi@shop.test", "address": "1 Main St", "address2": None,
    "city": "Town", "state": "Texas", "zipcode": "75001", "timezone": "America/Chicago", "opening_hours": HOURS,
}


class FakeClient:
    def __init__(self, info):
        self.info = info

    def store_info(self):
        return self.info


def test_store_info_shape():
    out = get_store_info({}, client=FakeClient(dict(API_INFO)), now=datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc))
    s = out["stores"][0]
    assert s["address"] == "1 Main St, Town, Texas, 75001"  # zip not in the address, so city/state/zip are added
    assert s["hours_summary"].startswith("Monday to Saturday")
    assert s["open_now"]["is_open"] is True


def test_store_info_address_already_complete_is_not_doubled():
    info = dict(API_INFO, address="1688 S Highland Ave STE C, Jackson , TN , 38301", city="Jackson", state="Tennessee", zipcode="38301")
    assert get_store_info({}, client=FakeClient(info))["stores"][0]["address"] == "1688 S Highland Ave STE C, Jackson, TN, 38301"


def test_store_info_missing_hours_and_phone():
    out = get_store_info({}, client=FakeClient(dict(API_INFO, opening_hours={}, phone="")))
    assert out["stores"][0]["hours"] is None and out["stores"][0]["phone"] is None


def test_store_info_none_args_and_api_down():
    from bot.api_client import ApiError
    assert get_store_info({}, client=FakeClient({"name": None, "phone": None, "email": None}))["found"] is False
    assert "error" in get_store_info({"x": 1}, client=FakeClient(dict(API_INFO)))

    class Down:
        def store_info(self):
            raise ApiError("down", retryable=True)
    assert "Do not guess" in get_store_info({}, client=Down())["error"]


def test_store_info_real_stores_response_end_to_end():
    from pathlib import Path
    from bot.api_client import ApiClient, ApiConfig

    class Resp:
        status = 200

        def read(self):
            return (Path(__file__).parent / "fixtures" / "api" / "stores.json").read_bytes()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    client = ApiClient(ApiConfig("http://x", "uuid"), opener=lambda req, timeout=None: Resp())
    out = get_store_info({}, client=client, now=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc))
    s = out["stores"][0]
    assert s["phone"] == "(731) 423-6565" and s["email"] == "southjacksonfurniture@gmail.com"
    assert s["address"] == "1688 S Highland Ave STE C, Jackson, TN, 38301"
    assert s["timezone"] == "America/New_York"
    assert s["hours_summary"] == "Monday to Saturday: 9:00 AM - 7:00 PM; Sunday: 11:00 AM - 6:00 PM"
    assert s["open_now"]["is_open"] is True  # Tuesday 10:00 AM in New York
    assert "secret" not in json.dumps(out).lower()


# ---------- get_shipping_options (answers from the store's policy text; no database, no prices of its own) ----------
CHUNK = {"title": "Shipping Policy", "heading": None, "page": "/shipping", "source_type": "policy",
         "source_key": "shipping", "similarity": 0.8, "content": "We ship within the USA. Local delivery is $299."}


class FakeSearch:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, args, ctx=None):
        self.calls.append((args, ctx))
        return self.result


def test_shipping_returns_the_store_policy_text_and_passes_the_context():
    search = FakeSearch({"found": True, "chunks": [dict(CHUNK)], "note": "x"})
    ctx = object()
    out = get_shipping_options({}, ctx=ctx, search=search)
    assert out["found"] is True and out["source"] == "store_policy_text" and out["chunks"] == [CHUNK]
    assert "delivery date" in out["note"] and "only if the text states it" in out["note"]
    (args, got_ctx), = search.calls
    assert got_ctx is ctx and args["source_types"] == ["policy", "guide", "faq"]


def test_shipping_keyword_is_added_to_the_question():
    search = FakeSearch({"found": False})
    out = get_shipping_options({"zone_keyword": "Canada"}, search=search)
    assert "Canada" in search.calls[0][0]["question"]
    assert out["found"] is False and "Do not guess" in out["note"]


def test_shipping_search_failure_is_unavailable_not_a_guess():
    out = get_shipping_options({}, search=FakeSearch({"error": "policy search is unavailable right now"}))
    assert out["error"] == "shipping_information_unavailable" and "chunks" not in out


def test_shipping_answers_from_the_policy_search_only():
    search = FakeSearch({"found": False})
    assert get_shipping_options({}, search=search)["found"] is False and search.calls    # see test_no_database_in_service.py


@pytest.mark.parametrize("bad", [{"country": "US"}, {"zone_keyword": 5}, {"zone_keyword": "x" * 300}])
def test_shipping_bad_input(bad):
    search = FakeSearch({"found": False})
    assert "error" in get_shipping_options(bad, search=search) and search.calls == []


def test_failed_store_lookup_is_not_cached_as_contact_text(monkeypatch):
    import bot.guardrails as g
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", None)
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a, ctx=None: {"error": "Store details are not available right now."})
    assert g.store_contact_text() == "" and g._STORE_CONTACT_TEXT is None
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a, ctx=None: {"found": True, "stores": [{"phone": "(731) 423-6565"}]})
    assert "423-6565" in g.store_contact_text()  # the next call succeeds and is cached

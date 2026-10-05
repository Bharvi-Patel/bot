import json

import pytest

from sjbot.config import full_image_url
from sjbot.tools.get_shipping_options import get_shipping_options
from sjbot.tools.get_store_info import get_store_info
from sjbot.tools.list_categories import list_categories

TREE = [
    {"category_id": 1, "parent_id": 0, "slug": "living-room", "title": "Living Room"},
    {"category_id": 2, "parent_id": 1, "slug": "sofas-and-seating", "title": "Sofas and Seating"},
    {"category_id": 3, "parent_id": 2, "slug": "sofas-and-seating-sectional-sofas", "title": "Sectional Sofas"},
    {"category_id": 4, "parent_id": 2, "slug": "sofas-and-seating-sofas", "title": "Sofas"},
    {"category_id": 5, "parent_id": 0, "slug": "bedroom", "title": "Bedroom"},
    {"category_id": 6, "parent_id": None, "slug": "outdoor", "title": "Outdoor"},
]


def tree_db(sql, params=()):
    assert "FROM vw_chat_categories" in sql
    return [dict(r) for r in TREE]


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
    out = list_categories({}, run_query=tree_db)
    assert [c["slug"] for c in out["categories"]] == ["bedroom", "living-room", "outdoor"]
    living = next(c for c in out["categories"] if c["slug"] == "living-room")
    assert living["has_subcategories"] is True
    assert next(c for c in out["categories"] if c["slug"] == "bedroom")["has_subcategories"] is False


def test_children_of_a_parent():
    out = list_categories({"parent_slug": "sofas-and-seating"}, run_query=tree_db)
    assert [c["title"] for c in out["categories"]] == ["Sectional Sofas", "Sofas"]
    assert out["parent"]["path"] == "Living Room > Sofas and Seating"


def test_unknown_parent():
    out = list_categories({"parent_slug": "nope"}, run_query=tree_db)
    assert out["found"] is False


def test_keyword_search_returns_paths_shallowest_first():
    out = list_categories({"keyword": "SOFA"}, run_query=tree_db)
    paths = [m["path"] for m in out["matches"]]
    assert "Living Room > Sofas and Seating > Sectional Sofas" in paths
    assert out["matches"][0]["title"] == "Sofas and Seating"


def test_keyword_no_hits_has_note():
    assert "No category" in list_categories({"keyword": "zzz"}, run_query=tree_db)["note"]


@pytest.mark.parametrize("bad", [{"parent_slug": "a", "keyword": "b"}, {"colour": "x"}, {"keyword": 5}, {"keyword": "x" * 300}])
def test_categories_bad_input(bad):
    assert "error" in list_categories(bad, run_query=tree_db)


# ---------- get_store_info ----------
from datetime import datetime, timezone

from sjbot.tools.get_store_info import format_hours, open_now

HOURS = {
    "friday": {"open": "09:00", "close": "19:00", "is_open": True},
    "monday": {"open": "09:00", "close": "19:00", "is_open": True},
    "sunday": {"open": "11:00", "close": "18:00", "is_open": True},
    "tuesday": {"open": "09:00", "close": "19:00", "is_open": True},
    "saturday": {"open": "09:00", "close": "19:00", "is_open": True},
    "thursday": {"open": "09:00", "close": "19:00", "is_open": True},
    "wednesday": {"open": "09:00", "close": "19:00", "is_open": True},
}

STORE = {
    "name": "Shop", "phone": "555-1234", "email": "hi@shop.test", "address": "1 Main St", "address2": None,
    "city": "Town", "state": "TX", "zipcode": "75001", "timezone": "America/Chicago",
    "opening_hours": json.dumps(HOURS),
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


def test_store_info_shape():
    out = get_store_info({}, run_query=lambda *_: [dict(STORE)], now=datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc))
    s = out["stores"][0]
    assert s["address"] == "1 Main St, Town, TX, 75001"
    assert s["hours_summary"].startswith("Monday to Saturday")
    assert s["open_now"]["is_open"] is True


def test_store_info_hours_plain_text_and_empty():
    out = get_store_info({}, run_query=lambda *_: [dict(STORE, opening_hours="Mon-Fri 9 to 5")])
    assert out["stores"][0]["hours"] is None and out["stores"][0]["hours_text"] == "Mon-Fri 9 to 5"
    out = get_store_info({}, run_query=lambda *_: [dict(STORE, opening_hours=None, phone="")])
    assert out["stores"][0]["hours"] is None and out["stores"][0]["phone"] is None


def test_store_info_none_and_args():
    assert get_store_info({}, run_query=lambda *_: [])["found"] is False
    assert "error" in get_store_info({"x": 1}, run_query=lambda *_: [])


# ---------- get_shipping_options ----------
SHIP = [
    {"zone_name": "USA", "method_name": "Ground", "flat_price": "49.00", "note": None},
    {"zone_name": "USA", "method_name": "Pickup", "flat_price": "0", "note": "At the store"},
    {"zone_name": "Canada", "method_name": "Freight", "flat_price": "call us", "note": None},
]


def test_shipping_groups_by_zone_and_converts_prices():
    out = get_shipping_options({}, run_query=lambda *_: [dict(r) for r in SHIP])
    usa = next(z for z in out["zones"] if z["zone"] == "USA")
    assert [m["price"] for m in usa["methods"]] == [49.0, 0.0]
    canada = next(z for z in out["zones"] if z["zone"] == "Canada")
    assert canada["methods"][0]["price"] == "call us"
    assert "delivery date" in out["note"]


def test_shipping_zone_filter_and_no_match():
    out = get_shipping_options({"zone_keyword": "usa"}, run_query=lambda *_: [dict(r) for r in SHIP])
    assert [z["zone"] for z in out["zones"]] == ["USA"]
    assert get_shipping_options({"zone_keyword": "mars"}, run_query=lambda *_: [dict(r) for r in SHIP])["found"] is False


@pytest.mark.parametrize("bad", [{"country": "US"}, {"zone_keyword": 5}, {"zone_keyword": "x" * 300}])
def test_shipping_bad_input(bad):
    assert "error" in get_shipping_options(bad, run_query=lambda *_: [])

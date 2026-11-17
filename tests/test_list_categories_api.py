from pathlib import Path

from bot.api_client import ApiClient, ApiConfig, ApiError
from bot.tools.list_categories import list_categories, rows_from_tree

FIX = Path(__file__).parent / "fixtures" / "api"


class Resp:
    status = 200

    def read(self):
        return (FIX / "categories_tree.json").read_bytes()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def real_client():
    return ApiClient(ApiConfig("http://x", "uuid"), opener=lambda req, timeout=None: Resp())


def test_real_tree_top_level():
    out = list_categories({}, client=real_client())
    assert [c["title"] for c in out["categories"]] == [
        "Bedroom", "Dining Room", "Home Office", "Kids and Nursery", "Living Room", "Mattresses and Bedding", "Outdoor", "Rugs and Decor"]
    assert all(c["has_subcategories"] for c in out["categories"] if c["slug"] == "dining-room")
    assert "Tell the customer these category names" in out["note"]


def test_real_tree_children_of_dining_room():
    out = list_categories({"parent_slug": "dining-room"}, client=real_client())
    assert out["parent"]["path"] == "Dining Room"
    kids = {c["slug"]: c["has_subcategories"] for c in out["categories"]}
    assert kids == {"dining-room-bar-furniture": True, "dining-room-dining-furniture": True, "dining-room-sets": False}


def test_real_tree_keyword_bed_ranks_beds_first_and_caps_at_ten():
    out = list_categories({"keyword": "bed"}, client=real_client())
    paths = [m["path"] for m in out["matches"]]
    assert "Bedroom > Bedroom Furniture > Beds" in paths
    assert len(paths) == 10 and out["total_matches"] == 13


def test_rows_from_tree_uses_position_for_parent():
    rows = rows_from_tree([{"category_id": 1, "slug": "a", "name": "A", "children": [
        {"category_id": 2, "slug": "b", "name": "B", "children": []}]}])
    assert rows == [{"category_id": 1, "parent_id": 0, "slug": "a", "title": "A"},
                    {"category_id": 2, "parent_id": 1, "slug": "b", "title": "B"}]


def test_api_down_gives_safe_error():
    class Down:
        def categories(self):
            raise ApiError("down", retryable=True)
    out = list_categories({}, client=Down())
    assert "Do not guess" in out["error"]

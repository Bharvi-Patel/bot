import json
from pathlib import Path

from bot.api_client import ApiClient, ApiConfig, ApiError
from bot.tools.list_brands import list_brands

FIX = Path(__file__).parent / "fixtures" / "api"


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def real_client():
    body = (FIX / "brands.json").read_bytes()
    return ApiClient(ApiConfig("http://x", "uuid"), opener=lambda req, timeout=None: Resp(body))


def test_real_brands_response_end_to_end():
    out = list_brands({}, client=real_client())
    assert out["found"] and out["count"] == 8
    assert [b["name"] for b in out["brands"]] == [
        "Acme", "Coaster", "Crownmark", "Emerald Home", "FOA Exclusive", "Furniture Of America", "Happy Home", "MDA Rugs"]
    assert {"slug": "foa", "name": "Furniture Of America"} in out["brands"]
    assert "products_available" not in json.dumps(out)
    assert "Name every brand" in out["note"]


def test_rejects_arguments():
    assert "error" in list_brands({"x": 1}, client=real_client())


def test_api_down_gives_safe_error_not_a_crash():
    class Down:
        def brands(self):
            raise ApiError("could not reach the store API", retryable=True)
    out = list_brands({}, client=Down())
    assert "error" in out and "Do not guess" in out["error"]


def test_switched_off_brand_is_skipped():
    class C:
        def brands(self):
            return [{"slug": "a", "name": "A", "status": 1}, {"slug": "b", "name": "B", "status": 0}]
    assert [b["slug"] for b in list_brands({}, client=C())["brands"]] == ["a"]


def test_empty_list():
    class C:
        def brands(self):
            return []
    assert list_brands({}, client=C())["found"] is False

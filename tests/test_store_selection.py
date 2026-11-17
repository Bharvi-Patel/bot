"""One bot, many stores: the API client is chosen by the chat's store, and stores never mix."""
import io
import json
import pathlib

import pytest

from bot import api_client, guardrails as g
from bot.api_client import ApiError, get_client
from bot.context import ChatContext

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def api(monkeypatch):
    """Stands in for the store API. Each store answers with its own name, chosen by the Store-UUID header."""
    seen = []

    def fake_urlopen(req, timeout=None):
        uuid = req.get_header("Store-uuid")
        seen.append(uuid)
        body = json.loads((FIXTURES / "stores.json").read_text())
        body["stores"]["name"] = f"Store {uuid}"
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(api_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("BOT_API_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.delenv("BOT_STORE_UUID", raising=False)
    return seen


def test_each_chat_talks_to_its_own_store(api):
    assert get_client(ChatContext(store_uuid="store-a")).store_info()["name"] == "Store store-a"
    assert get_client(ChatContext(store_uuid="store-b")).store_info()["name"] == "Store store-b"
    assert api == ["store-a", "store-b"]


def test_alternating_stores_never_mix(api):
    a, b = ChatContext(store_uuid="store-a"), ChatContext(store_uuid="store-b")
    names = [get_client(c).store_info()["name"] for c in (a, b, a, a, b)]
    assert names == ["Store store-a", "Store store-b", "Store store-a", "Store store-a", "Store store-b"]


def test_a_new_client_is_built_each_time(api):
    ctx = ChatContext(store_uuid="store-a")
    assert get_client(ctx) is not get_client(ctx)


def test_no_store_means_an_error_not_another_stores_data(api):
    with pytest.raises(ApiError, match="no store selected"):
        get_client(ChatContext())
    with pytest.raises(ApiError, match="no store selected"):
        get_client(None)
    assert api == []


def test_env_default_is_used_only_when_the_context_has_no_store(api, monkeypatch):
    monkeypatch.setenv("BOT_STORE_UUID", "dev-store")
    assert get_client(ChatContext()).store_info()["name"] == "Store dev-store"
    assert get_client(ChatContext(store_uuid="store-a")).store_info()["name"] == "Store store-a"


@pytest.mark.parametrize("bad", ["a b", "abc\r\nX-Evil: 1", "x" * 65, "a/b", "../x"])
def test_store_ids_with_odd_characters_are_refused(api, bad):
    with pytest.raises(ApiError, match="not allowed"):
        get_client(ChatContext(store_uuid=bad))
    assert api == []


def test_missing_base_url_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("BOT_API_BASE_URL", raising=False)
    with pytest.raises(ApiError, match="BOT_API_BASE_URL"):
        get_client(ChatContext(store_uuid="store-a"))


def test_run_tool_gives_the_tool_the_chats_store(api):
    out_a = g.run_tool("get_store_info", {}, ChatContext(store_uuid="store-a"))
    out_b = g.run_tool("get_store_info", {}, ChatContext(store_uuid="store-b"))
    assert api == ["store-a", "store-b"]
    assert "Store store-a" in json.dumps(out_a) and "Store store-b" in json.dumps(out_b)


def test_a_tool_run_with_no_store_fails_safe(api):
    out = g.run_tool("get_store_info", {}, ChatContext())
    assert "error" in out and api == []

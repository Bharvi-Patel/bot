"""Policy search reads one store's pages only. The tests use an in-memory stand-in for the RAG database."""
import json
import pathlib
import sys

import pytest

from bot import stores
from bot.context import ChatContext
from bot.store_config import ConfigError, StoreConfig
from bot.tools import search_policies as sp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CHUNKS = [
    {"id": 1, "store_uuid": "store-a", "source_type": "policy", "source_key": "returns", "title": "Returns A",
     "heading": "Returns A > h", "url_path": "/returns", "content": "Alpha accepts returns within 30 days.", "similarity": 0.9},
    {"id": 2, "store_uuid": "store-b", "source_type": "policy", "source_key": "returns", "title": "Returns B",
     "heading": "Returns B > h", "url_path": "/returns", "content": "Beta accepts returns within 7 days.", "similarity": 0.9},
]


class FakeDB:
    """Behaves like the SQL: only rows whose store_uuid matches the parameter after the vector come back."""
    def __init__(self):
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, params))
        assert "store_uuid = %s" in sql, "the store filter is missing from the query"
        store = params[1]
        return [r for r in CHUNKS if r["store_uuid"] == store] if "ORDER BY embedding" in sql else []


def embed(q):
    return [0.1, 0.2]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("BOT_STORE_UUID", raising=False)
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)
    stores.reset()
    yield
    stores.reset()


def test_each_store_gets_only_its_own_pages():
    db = FakeDB()
    a = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-a"))
    b = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-b"))
    assert [c["title"] for c in a["chunks"]] == ["Returns A"] and "30 days" in a["chunks"][0]["content"]
    assert [c["title"] for c in b["chunks"]] == ["Returns B"] and "7 days" in b["chunks"][0]["content"]


def test_a_store_with_no_pages_finds_nothing_instead_of_another_stores_pages():
    out = sp.search_policies({"question": "returns"}, run_query=FakeDB(), embed_query=embed, ctx=ChatContext(store_uuid="store-c"))
    assert out["found"] is False


def test_no_store_means_no_search_at_all():
    db = FakeDB()
    for ctx in (None, ChatContext(), ChatContext(store_uuid="bad id")):
        out = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ctx)
        assert "error" in out
    assert db.calls == []


def test_the_environment_default_is_used_only_when_the_chat_has_no_store(monkeypatch):
    monkeypatch.setenv("BOT_STORE_UUID", "store-b")
    db = FakeDB()
    out = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext())
    assert out["chunks"][0]["title"] == "Returns B"
    out = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-a"))
    assert out["chunks"][0]["title"] == "Returns A"


def test_both_queries_carry_the_store_and_in_the_right_place():
    db = FakeDB()
    sp.search_policies({"question": "return policy"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-a"))
    vector_sql, vector_params = db.calls[0]
    assert vector_params == ["[0.100000,0.200000]", "store-a", ["policy", "guide", "faq"], "[0.100000,0.200000]", 8]
    kw_sql, kw_params = db.calls[1]
    assert "store_uuid = %s" in kw_sql and kw_params[1] == "store-a" and kw_params[2] == ["policy", "guide", "faq"]


def test_scrub_terms_belong_to_the_store(tmp_path, monkeypatch):
    (tmp_path / "store-a.json").write_text(json.dumps(
        {"store_name": "Alpha", "scrub_terms": ["Acmeville"]}))
    (tmp_path / "store-b.json").write_text(json.dumps({"store_name": "Beta"}))
    monkeypatch.setenv("BOT_STORE_CONFIG_DIR", str(tmp_path))
    text = "Returns are free. Visit our Acmeville showroom for help. Thank you."
    rows = [{**CHUNKS[0], "content": text}, {**CHUNKS[1], "content": text}]
    db = lambda sql, params: [r for r in rows if r["store_uuid"] == params[1]] if "ORDER BY embedding" in sql else []
    a = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-a"))
    b = sp.search_policies({"question": "returns"}, run_query=db, embed_query=embed, ctx=ChatContext(store_uuid="store-b"))
    assert "Acmeville" not in a["chunks"][0]["content"] and "Returns are free." in a["chunks"][0]["content"]
    assert "Acmeville" in b["chunks"][0]["content"]                   # Beta did not ask for that term to be removed


def test_scrub_text_defaults_to_the_development_terms_and_accepts_others():
    assert "Brooklyn" not in sp.scrub_text("Fine. Our Brooklyn store is open. Done.")
    assert sp.scrub_text("Our Brooklyn store is open.", ()) == "Our Brooklyn store is open."
    assert "Queens" not in sp.scrub_text("Fine. See Queens. Done.", ("queens",))


@pytest.mark.parametrize("bad", [["a.*"], ["x" * 41], [""], ["line\nbreak"], ["(group)"], "brooklyn", ["a"] * 21])
def test_bad_scrub_terms_are_refused(bad):
    with pytest.raises(ConfigError):
        StoreConfig.from_dict("s-1", {"store_name": "A", "scrub_terms": bad})


def test_the_migration_marks_existing_rows_with_the_given_store_and_checks_the_id():
    from rag.migrate_add_store_uuid import statements
    sql = statements("store-a")
    assert sql[1] == ("UPDATE rag_chunks SET store_uuid = %s WHERE store_uuid IS NULL", ("store-a",))
    assert any("SET NOT NULL" in s for s, _ in sql) and any("CREATE INDEX" in s for s, _ in sql)
    with pytest.raises(ValueError):
        statements("../x")


def test_sync_only_adds_and_removes_what_changed():
    from rag.sync import plan_changes
    rows = [{"source_key": "returns", "content_hash": "h2"}, {"source_key": "faq-1", "content_hash": "h1"}]
    new, gone, _ = plan_changes(rows, {("returns", "h1"), ("faq-1", "h1"), ("old", "h9")})
    assert [r["content_hash"] for r in new] == ["h2"] and sorted(gone) == [("old", "h9"), ("returns", "h1")]

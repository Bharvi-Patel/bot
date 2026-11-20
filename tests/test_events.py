import json
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from bot import events, router, stores
from bot import guardrails as g
from bot.context import ChatContext
from bot.llm_base import LLMTurn, ToolCall
from bot.server import Limits, create_app

NOW = datetime(2026, 11, 19, 10, 30, 0, tzinfo=timezone.utc)


# ---------- summaries: counts and names only ----------

def test_a_search_is_summarized_without_its_keyword_or_products():
    args = {"keyword": "my secret sofa wish", "category_slug": "sofas", "brand_slug": "acme", "min_price": 100, "sort": "price_asc"}
    result = {"count": 3, "products": [{"sku": "A1", "name": "Acme Private Name", "price": 9.99}], "note": "free text"}
    ev = events.summarize_tool("search_products", args, result)
    assert ev == {"name": "search_products", "ok": True, "filters": ["keyword", "category", "brand", "price", "sort"],
                  "results": 3, "category": "sofas", "brand": "acme"}
    blob = json.dumps(ev)
    for private in ("secret sofa wish", "Acme Private Name", "A1", "9.99", "free text"):
        assert private not in blob


def test_a_zero_result_search_is_visible_as_zero():
    ev = events.summarize_tool("search_products", {"keyword": "xyz"}, {"count": 0, "products": []})
    assert ev["results"] == 0 and ev["ok"] is True


def test_a_failed_tool_keeps_only_a_machine_code():
    assert events.summarize_tool("search_products", {"category_slug": "nope"}, {"error": "unknown_category"})["error"] == "unknown_category"
    ev = events.summarize_tool("search_products", {"category_slug": "nope"}, {"error": "unknown parameter(s): email, my text"})
    assert ev["error"] == "other" and ev["ok"] is False and "results" not in ev and "category" not in ev     # a refused slug is not recorded


@pytest.mark.parametrize("slug", ["Has Space", "x" * 200, "UPPER", "semi;colon", "", None, 5, ["a"]])
def test_only_clean_catalog_slugs_are_recorded(slug):
    ev = events.summarize_tool("search_products", {"keyword": "sofa", "category_slug": slug, "brand_slug": slug}, {"count": 1, "products": []})
    assert "category" not in ev and "brand" not in ev


def test_policy_and_product_and_order_summaries_hold_only_counts_and_flags():
    assert events.summarize_tool("search_policies", {"question": "private words"}, {"found": True, "chunks": [{"content": "x"}] * 3}) == \
        {"name": "search_policies", "ok": True, "found": True, "results": 3}
    assert events.summarize_tool("search_policies", {}, {"found": False}) == {"name": "search_policies", "ok": True, "found": False, "results": 0}
    assert events.summarize_tool("get_product_details", {"sku": "A1"}, {"found": True, "product": {"name": "n"}}) == \
        {"name": "get_product_details", "ok": True, "found": True}
    orders = events.summarize_tool("get_order_status", {"order_number": "1001"}, {"found": True, "orders": [{"order_number": "1001"}] * 2})
    assert orders == {"name": "get_order_status", "ok": True, "found": True, "results": 2}
    assert "1001" not in json.dumps(orders)
    assert events.summarize_tool("get_order_status", {}, {"error": "login_required"}) == \
        {"name": "get_order_status", "ok": False, "error": "login_required"}


def test_other_tools_record_only_that_they_ran():
    assert events.summarize_tool("list_brands", {}, {"brands": [1, 2]}) == {"name": "list_brands", "ok": True}


def test_recommend_products_and_shipping_summaries_hold_only_counts_and_flags():
    rec = events.summarize_tool(
        "recommend_products",
        {"query": "firm mattress for my bad back", "max_price": 700, "category_slug": "mattresses", "brand_slug": "sealy"},
        {"found": True, "products": [{"sku": "M1", "name": "Secret Mattress"}] * 2},
    )
    assert rec == {"name": "recommend_products", "ok": True, "filters": ["category", "brand", "price"],
                   "found": True, "results": 2, "category": "mattresses", "brand": "sealy"}
    assert "bad back" not in json.dumps(rec) and "Secret Mattress" not in json.dumps(rec)
    assert events.summarize_tool("recommend_products", {"query": "anything"}, {"found": False}) == \
        {"name": "recommend_products", "ok": True, "filters": [], "found": False, "results": 0}
    ship = events.summarize_tool("get_shipping_options", {"zone_keyword": "Canada"}, {"found": True, "chunks": [{"content": "x"}] * 2})
    assert ship == {"name": "get_shipping_options", "ok": True, "found": True, "results": 2}
    assert "Canada" not in json.dumps(ship)


@pytest.mark.parametrize("args,result", [(None, None), ("text", [1]), ({"keyword": object()}, {"count": "5"}), ({}, {"count": True}),
                                         ({}, {"count": -3}), ({}, {"count": 10**9})])
def test_odd_inputs_never_break_a_summary(args, result):
    ev = events.summarize_tool("search_products", args, result)
    assert ev["name"] == "search_products" and ev.get("results", 0) == 0


def test_a_strange_tool_name_is_replaced():
    assert events.summarize_tool("Bad Name; DROP", {}, {})["name"] == "other"


# ---------- collecting ----------

def test_record_tool_does_nothing_without_a_collector():
    events.record_tool("search_products", {}, {"count": 1})                      # no error, nothing kept


def test_collect_gathers_calls_and_stops_after_the_block():
    with events.collect() as got:
        events.record_tool("list_brands", {}, {})
        events.record_tool("search_products", {"keyword": "a"}, {"count": 2})
    events.record_tool("list_brands", {}, {})
    assert [e["name"] for e in got] == ["list_brands", "search_products"]


def test_collections_in_different_threads_do_not_mix():
    seen = {}

    def work(name):
        with events.collect() as got:
            for _ in range(5):
                events.record_tool(name, {}, {})
        seen[name] = [e["name"] for e in got]
    threads = [threading.Thread(target=work, args=(n,)) for n in ("list_brands", "list_categories", "search_policies")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(v == [k] * 5 for k, v in seen.items())


def test_the_number_of_collected_calls_is_bounded():
    with events.collect() as got:
        for _ in range(100):
            events.record_tool("list_brands", {}, {})
    assert len(got) == events.MAX_TOOL_EVENTS


# ---------- the event ----------

def build(**over):
    base = dict(request_id="req-1", store="store-a", client="0123456789ab", conversation="conv-9", logged_in=False,
                outcome="answered", blocked=None, ms=42, tool_events=[{"name": "list_brands", "ok": True}], now=NOW)
    return events.build_chat_event(**{**base, **over})


def test_an_event_has_exactly_the_agreed_fields():
    assert build() == {"v": 1, "event": "chat_turn", "ts": "2026-11-19T10:30:00Z", "request_id": "req-1", "store": "store-a",
                       "client": "0123456789ab", "conversation": "conv-9", "logged_in": False, "outcome": "answered",
                       "blocked": None, "ms": 42, "tools": [{"name": "list_brands", "ok": True}]}


@pytest.mark.parametrize("field,bad", [("request_id", "has space"), ("store", "a b"), ("client", "1.2.3.4"), ("client", "ABCDEF123456"),
                                       ("conversation", "x;y"), ("blocked", "Some free text!"), ("outcome", "weird")])
def test_a_bad_field_is_dropped_or_replaced_never_written_as_given(field, bad):
    ev = build(**{field: bad})
    assert bad not in json.dumps(ev)
    if field == "outcome":
        assert ev["outcome"] == "error"


def test_the_time_is_utc_whatever_the_clock_says():
    local = datetime(2026, 11, 19, 20, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert build(now=local)["ts"] == "2026-11-19T14:30:00Z"


# ---------- the file sink ----------

def sink(tmp_path, days=90, clock=lambda: NOW):
    return events.JsonlEventSink(tmp_path / "ev", days, clock=clock)


def test_events_go_to_one_file_per_utc_day_one_json_line_each(tmp_path):
    s = sink(tmp_path)
    s.write(build())
    s.write(build(request_id="req-2"))
    files = sorted((tmp_path / "ev").iterdir())
    assert [f.name for f in files] == ["events-2026-11-19.jsonl"]
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert [r["request_id"] for r in rows] == ["req-1", "req-2"]


def test_a_new_day_starts_a_new_file(tmp_path):
    clock = {"now": NOW}
    s = sink(tmp_path, clock=lambda: clock["now"])
    s.write(build())
    clock["now"] = NOW + timedelta(days=1)
    s.write(build())
    assert sorted(f.name for f in (tmp_path / "ev").iterdir()) == ["events-2026-11-19.jsonl", "events-2026-11-20.jsonl"]


def test_old_files_are_deleted_and_nothing_else_is(tmp_path):
    d = tmp_path / "ev"
    d.mkdir()
    for name in ("events-2026-08-01.jsonl", "events-2026-08-20.jsonl", "events-2026-08-21.jsonl", "events-2026-11-18.jsonl",
                 "notes.txt", "events-2026-08-01.jsonl.bak", "events-2026-13-45.jsonl", "events-today.jsonl"):
        (d / name).write_text("x")
    (d / "events-2026-01-01.jsonl.d").mkdir()
    s = events.JsonlEventSink(d, 90, clock=lambda: NOW)           # 90 days before 2026-11-19 is 2026-08-21
    left = sorted(p.name for p in d.iterdir())
    assert "events-2026-08-01.jsonl" not in left and "events-2026-08-20.jsonl" not in left
    assert {"events-2026-08-21.jsonl", "events-2026-11-18.jsonl", "notes.txt", "events-2026-08-01.jsonl.bak",
            "events-2026-13-45.jsonl", "events-today.jsonl", "events-2026-01-01.jsonl.d"} <= set(left)
    assert s.prune() == 0


def test_pruning_happens_again_when_the_day_changes(tmp_path):
    clock = {"now": NOW}
    s = sink(tmp_path, days=2, clock=lambda: clock["now"])
    s.write(build())                                                # file for 11-19
    clock["now"] = NOW + timedelta(days=5)
    s.write(build())
    assert [f.name for f in (tmp_path / "ev").iterdir()] == ["events-2026-11-24.jsonl"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not report Unix permission bits")
def test_the_directory_and_files_are_private_to_the_owner(tmp_path):
    s = sink(tmp_path)
    s.write(build())
    f = next((tmp_path / "ev").iterdir())
    assert (tmp_path / "ev").stat().st_mode & 0o077 == 0 and f.stat().st_mode & 0o077 == 0


def test_a_write_problem_is_logged_not_raised(tmp_path, caplog):
    s = sink(tmp_path)
    (tmp_path / "ev" / "events-2026-11-19.jsonl").mkdir()          # a directory where the file should be
    caplog.set_level(logging.WARNING)
    s.write(build())
    s.write(build())
    assert len([r for r in caplog.records if "could not write funnel event" in r.getMessage()]) == 1    # first failure only


def test_many_threads_write_whole_lines(tmp_path):
    s = sink(tmp_path)
    threads = [threading.Thread(target=lambda i=i: [s.write(build(request_id=f"r{i}-{j}")) for j in range(20)]) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    lines = (tmp_path / "ev" / "events-2026-11-19.jsonl").read_text().splitlines()
    assert len(lines) == 200 and all(json.loads(x)["event"] == "chat_turn" for x in lines)


def test_a_retention_below_one_day_is_refused(tmp_path):
    with pytest.raises(ValueError):
        events.JsonlEventSink(tmp_path, 0)


# ---------- settings from the environment ----------

def test_events_are_off_without_a_directory(monkeypatch):
    monkeypatch.delenv("BOT_EVENTS_DIR", raising=False)
    s = events.sink_from_env()
    assert isinstance(s, events.NullEventSink) and s.write(build()) is None


def test_the_directory_and_retention_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_EVENTS_DIR", str(tmp_path / "e"))
    s = events.sink_from_env()
    assert isinstance(s, events.JsonlEventSink) and s.retention_days == 90
    monkeypatch.setenv("BOT_EVENTS_RETENTION_DAYS", "30")
    assert events.sink_from_env().retention_days == 30


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "1.5"])
def test_a_bad_retention_stops_the_service_from_starting(monkeypatch, tmp_path, bad):
    monkeypatch.setenv("BOT_EVENTS_DIR", str(tmp_path / "e"))
    monkeypatch.setenv("BOT_EVENTS_RETENTION_DAYS", bad)
    with pytest.raises(ValueError):
        events.sink_from_env()


# ---------- the router reports what it ran ----------

class FakeLLM:
    def __init__(self, *turns):
        self.turns = list(turns)

    def generate(self, messages):
        return self.turns.pop(0)


@pytest.fixture
def quiet_router(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")


def test_the_router_reports_each_tool_it_ran(monkeypatch, quiet_router):
    monkeypatch.setitem(g.TOOLS, "search_products", lambda a, ctx=None: {"count": 2, "products": [
        {"sku": "A1", "name": "Acme Sofa", "price": 499.0}, {"sku": "A2", "name": "Acme Loveseat", "price": 399.0}]})
    llm = FakeLLM(LLMTurn(tool_calls=[ToolCall("search_products", {"keyword": "sofa", "max_price": 600})]),
                  LLMTurn(text="Here are two options: Acme Sofa $499.00 and Acme Loveseat $399.00."))
    with events.collect() as got:
        out = router.answer("sofas under 600 please", llm)
    assert out["blocked"] is None
    assert got == [{"name": "search_products", "ok": True, "filters": ["keyword", "price"], "results": 2}]
    assert "sofas under 600" not in json.dumps(got) and "Acme" not in json.dumps(got)


def test_a_repeated_identical_call_is_not_counted_twice(monkeypatch, quiet_router):
    monkeypatch.setitem(g.TOOLS, "list_brands", lambda a, ctx=None: {"brands": []})
    llm = FakeLLM(LLMTurn(tool_calls=[ToolCall("list_brands", {})]), LLMTurn(tool_calls=[ToolCall("list_brands", {})]),
                  LLMTurn(text="We carry a range of brands."))
    with events.collect() as got:
        router.answer("what brands do you have", llm)
    assert [e["name"] for e in got] == ["list_brands"]


def test_the_router_output_is_unchanged_by_the_hook(monkeypatch, quiet_router):
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a, ctx=None: {"phone": "555-0100", "email": "info@store.com"})
    out = router.answer("what's your number?", FakeLLM(LLMTurn(tool_calls=[ToolCall("get_store_info", {})]), LLMTurn(text="Call us on 555-0100.")))
    assert out == {"reply": "Call us on 555-0100.", "tools_used": ["get_store_info"], "blocked": None}


# ---------- the service writes one event per turn ----------

KEY = "s3cret-key-for-tests"
GOOD = {"X-Bot-Key": KEY, "Store-UUID": "store-a", "X-Client-Id": "203.0.113.7"}


class ListSink:
    enabled = True

    def __init__(self):
        self.rows = []

    def write(self, event):
        self.rows.append(event)


def make(answer, sink_=None, **kw):
    sink_ = sink_ if sink_ is not None else ListSink()
    app = create_app(answer_fn=answer, llm_factory=lambda: "LLM", service_key=KEY, limits=kw.pop("limits", Limits()), events_sink=sink_)
    return TestClient(app), sink_


@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("BOT_EVENTS_DIR", raising=False)
    monkeypatch.setenv("BOT_ALLOW_DEV_DEFAULTS", "1")
    stores.reset()
    yield
    stores.reset()


def answer_with_search(message, llm, history, ctx=None):
    events.record_tool("search_products", {"keyword": "sofa", "category_slug": "sofas"}, {"count": 4, "products": []})
    return {"reply": "PRIVATE REPLY TEXT", "tools_used": ["search_products"], "blocked": None}


def test_a_chat_writes_one_event_with_the_turns_facts_and_no_text():
    c, sink_ = make(answer_with_search)
    r = c.post("/chat", json={"message": "MY PRIVATE QUESTION", "history": [{"role": "user", "text": "EARLIER WORDS"}]},
               headers={**GOOD, "X-Conversation-Id": "conv-77", "X-Customer-Uuid": "CUST-SECRET"})
    assert r.status_code == 200 and len(sink_.rows) == 1
    ev = sink_.rows[0]
    assert ev["event"] == "chat_turn" and ev["store"] == "store-a" and ev["conversation"] == "conv-77" and ev["logged_in"] is True
    assert ev["outcome"] == "answered" and ev["blocked"] is None and ev["ms"] >= 0
    assert ev["tools"] == [{"name": "search_products", "ok": True, "filters": ["keyword", "category"], "results": 4, "category": "sofas"}]
    assert ev["request_id"] == r.headers["X-Request-Id"]                 # the same id as the access-log line
    blob = json.dumps(ev)
    for private in ("MY PRIVATE QUESTION", "EARLIER WORDS", "PRIVATE REPLY TEXT", "CUST-SECRET", "203.0.113.7", KEY):
        assert private not in blob


def test_a_blocked_reply_is_an_event_with_its_reason():
    c, sink_ = make(lambda m, l, h, ctx=None: {"reply": "no", "tools_used": [], "blocked": "extraction_attempt"})
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert sink_.rows[0]["outcome"] == "blocked" and sink_.rows[0]["blocked"] == "extraction_attempt" and sink_.rows[0]["tools"] == []


def test_a_crash_is_an_error_event_and_still_a_502():
    def boom(m, l, h, ctx=None):
        events.record_tool("list_brands", {}, {})
        raise RuntimeError("secret details")
    c, sink_ = make(boom)
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 502
    ev = sink_.rows[0]
    assert ev["outcome"] == "error" and [t["name"] for t in ev["tools"]] == ["list_brands"] and "secret details" not in json.dumps(ev)


def test_refused_requests_write_no_event():
    c, sink_ = make(answer_with_search, limits=Limits(per_client=1, per_store=100))
    c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Bot-Key": "wrong-key"})            # 401
    c.post("/chat", json={"message": ""}, headers=GOOD)                                                # 422
    c.post("/chat", json={"message": "hello"}, headers=GOOD)                                           # 200: the only event
    c.post("/chat", json={"message": "hello"}, headers=GOOD)                                           # 429
    assert len(sink_.rows) == 1


def test_without_a_conversation_header_the_field_is_empty_and_a_bad_one_is_ignored():
    c, sink_ = make(answer_with_search)
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Conversation-Id": "bad id;x"})
    assert [r["conversation"] for r in sink_.rows] == [None, None]


def test_visitors_are_logged_out_in_the_event():
    c, sink_ = make(answer_with_search)
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert sink_.rows[0]["logged_in"] is False


def test_a_failing_sink_never_breaks_a_chat():
    class Broken:
        enabled = True

        def write(self, event):
            raise OSError("disk full")
    c, _ = make(answer_with_search, sink_=Broken())
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


def test_events_are_written_to_files_when_the_directory_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_EVENTS_DIR", str(tmp_path / "ev"))
    app = create_app(answer_fn=answer_with_search, llm_factory=lambda: "LLM", service_key=KEY, limits=Limits())
    TestClient(app).post("/chat", json={"message": "hello"}, headers=GOOD)
    files = list((tmp_path / "ev").glob("events-*.jsonl"))
    assert len(files) == 1 and json.loads(files[0].read_text().splitlines()[0])["tools"][0]["results"] == 4


def test_events_are_off_by_default():
    app = create_app(answer_fn=answer_with_search, llm_factory=lambda: "LLM", service_key=KEY, limits=Limits())
    assert TestClient(app).post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200      # no BOT_EVENTS_DIR: no files, no error

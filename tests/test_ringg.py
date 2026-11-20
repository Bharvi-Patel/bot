"""Ringg.AI call events: secret, store lookup, repeats, and that no customer data reaches the event line."""
import json

import pytest

from bot import ringg

STORE = "store-a"
AGENT = "00000000-0000-4000-8000-0000000000a1"
STORES = {AGENT: STORE}

# Shaped like the examples in Ringg's webhook docs (call_completed / all_processing_completed).
COMPLETED = {
    "event_type": "call_completed", "call_id": "00000000-0000-4000-8000-0000000000c4", "parent_call_id": None,
    "call_sid": "00000000-0000-4000-8000-0000000000c2", "agent_id": AGENT, "workspace_id": "w1",
    "status": "completed", "sub_status": "ACCEPTED", "call_type": "webcall", "call_duration": 15.73,
    "agent_message_count": 3, "user_message_count": 2,
    "custom_args_values": {"callee_name": "Priya Sharma", "conversation_id": "conv-9", "email": "priya@example.com"},
    "to_number": "+919876543210", "from_number": "+919876540111", "called_on": "2026-08-04T12:29:13Z",
    "call_cost": 0.15, "recording_url": "https://recordings.example.com/x.mp3",
    "transcript": [{"bot": "Hi Priya"}, {"user": "My card number is 4111 1111 1111 1111"}],
    "tool_call_logs": [{"tool": "search_products", "args": {"q": "firm mattress"}}],
}
ALL_DONE = {**COMPLETED, "event_type": "all_processing_completed",
            "platform_analysis": {"summary": "Priya wants a callback about her order", "classification": "callback_requested",
                                  "callback_requested": True, "callback_requested_time": "2026-08-05T10:00:00Z", "key_points": ["secret detail"]}}


def handle(payload, stores=STORES, seen=None):
    return ringg.handle(payload, stores, seen or ringg.Seen())


def test_a_completed_call_becomes_one_event_with_the_agreed_fields():
    what, ev = handle(COMPLETED)
    assert what == "recorded"
    ev.pop("ts")
    assert ev == {"v": 1, "event": "call_event", "store": STORE, "call_id": COMPLETED["call_id"], "parent_call_id": None,
                  "conversation": "conv-9", "ringg_event": "call_completed", "call_type": "webcall", "status": "completed",
                  "sub_status": "accepted", "duration_s": 15.73, "cost": 0.15, "callback_requested": None,
                  "classification": None, "agent_messages": 3, "user_messages": 2}


def test_no_customer_data_reaches_the_event():
    text = json.dumps([handle(COMPLETED)[1], handle(ALL_DONE)[1]])
    for private in ("+9198", "Priya", "priya@example.com", "4111", "firm mattress", "recordings.example.com", "secret detail",
                    "callback about her order", "2026-08-05"):
        assert private not in text


def test_analysis_gives_callback_and_classification_only():
    _, ev = handle(ALL_DONE)
    assert ev["callback_requested"] is True and ev["classification"] == "callback_requested" and ev["ringg_event"] == "all_processing_completed"


def test_the_store_is_limited_to_the_agents_allowed_stores_whatever_the_payload_says():
    evil = {**COMPLETED, "custom_args_values": {"store_uuid": "store-evil", "store": "store-evil"}}
    assert handle(evil) == ("unknown_agent", None)                                     # a store this agent may not serve: dropped
    assert handle(COMPLETED)[1]["store"] == STORE                                      # no store sent: the agent's one store
    assert handle({**COMPLETED, "custom_args_values": {"store_uuid": STORE}})[1]["store"] == STORE
    assert handle({**COMPLETED, "agent_id": "someone-elses-agent"})[0] == "unknown_agent"
    assert handle({**COMPLETED, "agent_id": None})[0] == "unknown_agent"
    assert handle(COMPLETED, stores={})[0] == "unknown_agent"                          # no map at all: nothing is recorded


def test_the_same_event_twice_is_recorded_once_but_other_events_of_that_call_are_kept():
    seen = ringg.Seen()
    assert handle(COMPLETED, seen=seen)[0] == "recorded"
    assert handle(COMPLETED, seen=seen)[0] == "duplicate"
    assert handle(ALL_DONE, seen=seen)[0] == "recorded"


def test_the_seen_list_is_bounded():
    seen = ringg.Seen(limit=3)
    for i in range(10):
        assert seen.first_time(f"k{i}")
    assert len(seen._items) == 3
    assert seen.first_time("k0")            # forgotten long ago


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"event_type": "call_completed"}, {"call_id": "c1"},
                                     {"call_id": "has space", "event_type": "call_completed"}, {"call_id": "c1", "event_type": 5}])
def test_things_that_are_not_ringg_events_are_bad_payloads(payload):
    assert handle(payload)[0] == "bad_payload"


@pytest.mark.parametrize("kind", ["call_ringing", "call_ongoing", "chat_completed", "recording_completed", "client_analysis_completed", "weird"])
def test_other_event_types_are_ignored(kind):
    assert handle({**COMPLETED, "event_type": kind})[0] == "ignored_type"


@pytest.mark.parametrize("field,bad", [("call_id", "x y"), ("parent_call_id", "a;b"), ("status", "hacked"), ("call_type", "fax"),
                                       ("sub_status", "Free text, with: punctuation!"), ("call_duration", -5), ("call_duration", "7"),
                                       ("call_cost", True)])
def test_a_bad_field_is_dropped_never_written_as_given(field, bad):
    what, ev = handle({**COMPLETED, field: bad})
    if field == "call_id":
        assert what == "bad_payload"
        return
    assert json.dumps(bad) not in json.dumps(ev)


def test_a_bad_message_count_becomes_zero():
    assert handle({**COMPLETED, "agent_message_count": -1, "user_message_count": "many"})[1]["agent_messages"] == 0
    assert handle({**COMPLETED, "user_message_count": "many"})[1]["user_messages"] == 0


def test_a_callback_links_to_its_parent_call():
    _, ev = handle({**COMPLETED, "parent_call_id": "orig-call-1"})
    assert ev["parent_call_id"] == "orig-call-1"


def test_failure_sub_status_with_spaces_is_kept_lowercase():
    _, ev = handle({**COMPLETED, "status": "failed", "sub_status": "Busy Line"})
    assert ev["status"] == "failed" and ev["sub_status"] == "busy line"


def test_secret_check():
    assert ringg.secret_ok("s3", "s3")
    assert not ringg.secret_ok("s4", "s3") and not ringg.secret_ok(None, "s3") and not ringg.secret_ok("", "")
    assert not ringg.secret_ok("anything", "")                       # no secret configured never matches


def test_agent_store_settings(monkeypatch):
    monkeypatch.delenv("RINGG_AGENT_STORES", raising=False)
    assert ringg.agent_stores_from_env() == {}
    monkeypatch.setenv("RINGG_AGENT_STORES", json.dumps(STORES))
    assert ringg.agent_stores_from_env() == STORES
    shared = {AGENT: [STORE, "store-b"]}
    monkeypatch.setenv("RINGG_AGENT_STORES", json.dumps(shared))
    assert ringg.agent_stores_from_env() == shared
    for bad in ("not json", "[]", json.dumps({"a b": "s"}), json.dumps({"a": 5}), json.dumps({"a": []}),
                json.dumps({"a": ["ok", "no good"]}), json.dumps({"a": ["ok", 5]})):
        monkeypatch.setenv("RINGG_AGENT_STORES", bad)
        with pytest.raises(ValueError):
            ringg.agent_stores_from_env()


# ---------- over HTTP (needs fastapi, like tests/test_server.py) ----------
def _client(secret="whsec", stores=STORES, sink=None):
    from fastapi.testclient import TestClient
    from bot.server import create_app

    class Sink:
        enabled = True

        def __init__(self):
            self.rows = []

        def write(self, event):
            self.rows.append(event)

    sink = sink or Sink()
    return TestClient(create_app(answer_fn=lambda *a, **k: {}, llm_factory=lambda: "LLM", service_key="k",
                                 events_sink=sink, ringg_secret=secret, ringg_agent_stores=stores)), sink


def test_http_accepts_a_good_event_and_writes_it():
    c, sink = _client()
    r = c.post("/webhooks/ringg", json=COMPLETED, headers={"X-Webhook-Secret": "whsec"})
    assert r.status_code == 200 and r.json() == {"received": True}
    assert len(sink.rows) == 1 and sink.rows[0]["event"] == "call_event" and sink.rows[0]["store"] == STORE


def test_http_repeat_is_acknowledged_but_not_written_twice():
    c, sink = _client()
    for _ in range(2):
        assert c.post("/webhooks/ringg", json=COMPLETED, headers={"X-Webhook-Secret": "whsec"}).status_code == 200
    assert len(sink.rows) == 1


@pytest.mark.parametrize("headers", [{}, {"X-Webhook-Secret": "wrong"}, {"X-Bot-Key": "k"}])
def test_http_without_the_webhook_secret_is_refused_and_nothing_is_written(headers):
    c, sink = _client()
    assert c.post("/webhooks/ringg", json=COMPLETED, headers=headers).status_code == 401
    assert sink.rows == []


def test_http_refuses_everything_when_no_secret_is_configured():
    c, sink = _client(secret="")
    assert c.post("/webhooks/ringg", json=COMPLETED, headers={"X-Webhook-Secret": ""}).status_code == 503
    assert sink.rows == []


def test_http_bad_body_is_a_400_and_unknown_agent_or_type_is_a_quiet_200():
    c, sink = _client()
    h = {"X-Webhook-Secret": "whsec"}
    assert c.post("/webhooks/ringg", content=b"not json", headers=h).status_code == 400
    assert c.post("/webhooks/ringg", json=[1], headers=h).status_code == 400
    assert c.post("/webhooks/ringg", json={**COMPLETED, "agent_id": "other"}, headers=h).status_code == 200
    assert c.post("/webhooks/ringg", json={**COMPLETED, "event_type": "call_ringing"}, headers=h).status_code == 200
    assert sink.rows == []


def test_http_accepts_a_transcript_sized_body_but_chat_keeps_its_small_limit():
    c, sink = _client()
    big = {**COMPLETED, "transcript": [{"bot": "x" * 1000}] * 100}            # about 100 KB
    assert c.post("/webhooks/ringg", json=big, headers={"X-Webhook-Secret": "whsec"}).status_code == 200
    r = c.post("/chat", json={"message": "x" * 20000}, headers={"X-Bot-Key": "k", "Store-UUID": "store-a", "X-Client-Id": "1.2.3.4"})
    assert r.status_code == 413


# ---------- the voice agent's tools ----------
def test_voice_tools_never_include_orders_and_are_all_real_tools():
    from bot.guardrails import TOOLS
    assert "get_order_status" not in ringg.VOICE_TOOLS
    assert set(ringg.VOICE_TOOLS) <= set(TOOLS)
    assert set(TOOLS) - set(ringg.VOICE_TOOLS) == {"get_order_status"}      # a new tool is a conscious decision: add it here or not


def test_a_tool_event_holds_only_counts_and_ids():
    from bot import events
    summary = events.summarize_tool("search_products", {"keyword": "firm mattress", "max_price": 600}, {"count": 4})
    ev = ringg.build_tool_event(STORE, "call-1", summary, 83)
    ev.pop("ts")
    assert ev == {"v": 1, "event": "call_tool", "store": STORE, "call_id": "call-1", "ms": 83, "tool": summary}
    assert "firm mattress" not in json.dumps(ev)
    bad = ringg.build_tool_event("a b", "x y", {"weird": 1}, -4)
    assert bad["store"] is None and bad["call_id"] is None and bad["ms"] == 0


def test_tools_key_setting(monkeypatch):
    monkeypatch.delenv("RINGG_TOOLS_KEY", raising=False)
    assert ringg.tools_key_from_env() == ""
    monkeypatch.setenv("RINGG_TOOLS_KEY", " k1 ")
    assert ringg.tools_key_from_env() == "k1"


def _tools_client(calls, key="toolkey", stores=STORES, result=None):
    from fastapi.testclient import TestClient
    from bot.server import create_app

    class Sink:
        enabled = True

        def __init__(self):
            self.rows = []

        def write(self, event):
            self.rows.append(event)

    sink = Sink()

    def tool_fn(name, args, ctx):
        calls.append((name, args, ctx))
        return result if result is not None else {"count": 2, "products": []}

    app = create_app(answer_fn=lambda *a, **k: {}, llm_factory=lambda: "L", service_key="k", events_sink=sink,
                     ringg_secret="whsec", ringg_agent_stores=stores, ringg_tools_key=key, tool_fn=tool_fn)
    return TestClient(app), sink


TH = {"X-Ringg-Tools-Key": "toolkey", "X-Ringg-Agent-Id": AGENT, "X-Ringg-Call-Id": "call-1"}


def test_http_tool_runs_with_the_agents_store_and_a_visitor_context():
    calls = []
    c, sink = _tools_client(calls)
    r = c.post("/ringg/tools/search_products", json={"keyword": "sofa", "store_uuid": "store-evil"}, headers=TH)
    assert r.status_code == 200 and r.json()["count"] == 2
    name, args, ctx = calls[0]
    assert name == "search_products" and args["keyword"] == "sofa"
    assert ctx.store_uuid == STORE and ctx.customer_uuid is None and ctx.customer_token is None     # store from the agent, never the body
    assert sink.rows[0]["event"] == "call_tool" and sink.rows[0]["call_id"] == "call-1" and "sofa" not in json.dumps(sink.rows)


def test_http_orders_are_not_available_by_voice():
    calls = []
    c, _ = _tools_client(calls)
    assert c.post("/ringg/tools/get_order_status", json={"order_number": "1"}, headers=TH).status_code == 404
    assert c.post("/ringg/tools/not_a_tool", json={}, headers=TH).status_code == 404
    assert calls == []


@pytest.mark.parametrize("headers,status", [({}, 401), ({"X-Ringg-Tools-Key": "wrong", "X-Ringg-Agent-Id": AGENT}, 401),
                                            ({"X-Bot-Key": "k", "X-Ringg-Agent-Id": AGENT}, 401),
                                            ({"X-Ringg-Tools-Key": "toolkey"}, 403),
                                            ({"X-Ringg-Tools-Key": "toolkey", "X-Ringg-Agent-Id": "other-agent"}, 403)])
def test_http_tool_needs_the_key_and_a_known_agent(headers, status):
    calls = []
    c, _ = _tools_client(calls)
    assert c.post("/ringg/tools/list_brands", json={}, headers=headers).status_code == status
    assert calls == []


def test_http_tools_refuse_everything_when_no_key_is_configured():
    calls = []
    c, _ = _tools_client(calls, key="")
    assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "X-Ringg-Tools-Key": ""}).status_code == 503
    assert calls == []


def test_http_empty_body_is_fine_for_list_tools_and_a_non_object_is_refused():
    calls = []
    c, _ = _tools_client(calls)
    assert c.post("/ringg/tools/list_brands", headers=TH).status_code == 200
    assert calls[0][1] == {}
    assert c.post("/ringg/tools/list_brands", content=b"nope", headers=TH).status_code == 400
    assert c.post("/ringg/tools/list_brands", json=[1], headers=TH).status_code == 400


def test_http_tool_failures_are_returned_to_the_agent_not_raised():
    calls = []
    c, sink = _tools_client(calls, result={"error": "tool_failed", "note": "Do not guess."})
    r = c.post("/ringg/tools/search_products", json={}, headers=TH)
    assert r.status_code == 200 and r.json()["error"] == "tool_failed"
    assert sink.rows[0]["tool"]["ok"] is False


def test_clean_tool_args_drops_blank_rows_and_coerces_numeric_strings():
    raw = {
        "keyword": "sofa",
        "category_slug": "",
        "brand_slug": "  ",
        "min_price": "null",
        "max_price": "500",
        "max_width_in": "60.5",
        "limit": "5",
        "color": None,
    }
    assert ringg.clean_tool_args(raw) == {
        "keyword": "sofa",
        "max_price": 500,
        "max_width_in": 60.5,
        "limit": 5,
    }
    assert ringg.clean_tool_args({"keyword": "sofa", "max_price": ""}) == {"keyword": "sofa"}
    assert ringg.clean_tool_args({"keyword": "sofa", "min_price": 0, "max_width_in": 0, "sort": "name_asc"}) == {
        "keyword": "sofa",
    }
    assert ringg.clean_tool_args({
        "category_slug": "living-room-sofas",
        "max_price": 800,
        "color": "any",
        "material": "N/A",
        "attribute_group": "none",
        "attribute_value": "none",
    }) == {
        "category_slug": "living-room-sofas",
        "max_price": 800,
    }
    assert ringg.clean_tool_args({
        "keyword": "sofa",
        "color": "blue",
        "material": "fabric",
        "attribute_group": "Bed Size",
        "attribute_value": "Queen",
        "store_uuid": "store-123",
    }) == {
        "keyword": "sofa",
        "color": "blue",
    }


def test_resolve_store_accepts_a_sent_store_only_from_the_agents_allowed_list():
    one = {AGENT: STORE}
    assert ringg.resolve_store(AGENT, one, STORE) == STORE
    assert ringg.resolve_store(AGENT, one, "other-store-999") is None                  # not this agent's store: refused
    assert ringg.resolve_store(AGENT, one, "") == STORE                                # blank or placeholder: the agent's one store
    assert ringg.resolve_store(AGENT, one, "null") == STORE
    assert ringg.resolve_store(AGENT, one, None) == STORE
    assert ringg.resolve_store("unknown", one, None) is None
    assert ringg.resolve_store("unknown", one, STORE) is None                          # an unlisted agent cannot name a store
    assert ringg.resolve_store(AGENT, {}, STORE) is None                               # empty map: refused, never trusted
    assert ringg.resolve_store(None, one, STORE) is None


def test_a_shared_agent_must_name_one_of_its_stores():
    shared = {AGENT: [STORE, "dynamic-store-1"]}
    assert ringg.resolve_store(AGENT, shared, "dynamic-store-1") == "dynamic-store-1"
    assert ringg.resolve_store(AGENT, shared, STORE) == STORE
    assert ringg.resolve_store(AGENT, shared, "store-evil") is None
    assert ringg.resolve_store(AGENT, shared, None) is None                            # several allowed, none named: no guess
    assert ringg.resolve_store(AGENT, {AGENT: []}, STORE) is None
    what, ev = ringg.handle({**COMPLETED, "custom_args_values": {"store_uuid": "dynamic-store-1", "conversation_id": "c1"}},
                            shared, ringg.Seen())
    assert what == "recorded" and ev["store"] == "dynamic-store-1" and ev["conversation"] == "c1"
    assert ringg.handle({**COMPLETED, "custom_args_values": {"store_uuid": "store-evil"}}, shared, ringg.Seen()) == ("unknown_agent", None)


def test_http_tool_store_header_must_be_an_allowed_store():
    calls = []
    shared = {AGENT: [STORE, "dynamic-store-1"]}
    c, _ = _tools_client(calls, stores=shared)
    ok = c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "dynamic-store-1"})
    assert ok.status_code == 200 and calls[-1][2].store_uuid == "dynamic-store-1"
    n = len(calls)
    assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "store-evil"}).status_code == 403
    assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "X-Store-Uuid": "store-evil"}).status_code == 403
    assert c.post("/ringg/tools/list_brands", json={}, headers=TH).status_code == 403          # shared agent, no store named
    assert len(calls) == n                                                                       # no tool ran for any refusal


def test_http_tool_with_no_agent_map_is_refused():
    calls = []
    c, _ = _tools_client(calls, stores={})
    assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "store-evil"}).status_code == 403
    assert calls == []

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
    assert ringg.clean_tool_args({
        "keyword": "tv",
        "attribute_group": "Screen Size",
        "attribute_value": "65 in",
    }) == {
        "keyword": "tv",
        "attribute_group": "Screen Size",
        "attribute_value": "65 in",
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


def test_per_agent_tools_keys_from_env_and_check(monkeypatch):
    monkeypatch.delenv("RINGG_TOOLS_KEY", raising=False)
    assert ringg.tools_key_from_env() == ""
    monkeypatch.setenv("RINGG_TOOLS_KEY", "shared-secret")
    assert ringg.tools_key_from_env() == "shared-secret"
    assert ringg.tools_key_ok("shared-secret", "any-agent", "shared-secret")
    assert not ringg.tools_key_ok("wrong", "any-agent", "shared-secret")

    per_agent = {"agent-a": "key-a", "agent-b": "key-b"}
    monkeypatch.setenv("RINGG_TOOLS_KEY", json.dumps(per_agent))
    loaded = ringg.tools_key_from_env()
    assert loaded == per_agent
    assert ringg.tools_key_ok("key-a", "agent-a", loaded)
    assert not ringg.tools_key_ok("key-a", "agent-b", loaded)
    assert not ringg.tools_key_ok("key-a", "unknown", loaded)
    assert not ringg.tools_key_ok("key-a", None, loaded)

    for bad in ("{not json", "{}", json.dumps({"bad id": "k"}), json.dumps({"agent-a": ""})):
        monkeypatch.setenv("RINGG_TOOLS_KEY", bad)
        with pytest.raises(ValueError):
            ringg.tools_key_from_env()


def test_http_tool_respects_per_agent_keys_and_disabled_store_config(monkeypatch, tmp_path):
    from bot import stores
    (tmp_path / f"{STORE}.json").write_text(json.dumps({"store_name": "Enabled Store", "enabled": True}))
    (tmp_path / "disabled-store.json").write_text(json.dumps({"store_name": "Off Store", "enabled": False}))
    monkeypatch.setenv("BOT_STORE_CONFIG_DIR", str(tmp_path))
    stores.reset()
    try:
        calls = []
        c, _ = _tools_client(
            calls,
            stores={"agent-a": [STORE, "disabled-store", "missing-store"], "agent-b": [STORE]},
            key={"agent-a": "key-a", "agent-b": "key-b"},
        )
        assert c.post("/ringg/tools/list_brands", json={}, headers={"X-Ringg-Tools-Key": "key-a", "X-Ringg-Agent-Id": "agent-a", "Store-UUID": STORE}).status_code == 200
        assert c.post("/ringg/tools/list_brands", json={}, headers={"X-Ringg-Tools-Key": "key-a", "X-Ringg-Agent-Id": "agent-b", "Store-UUID": STORE}).status_code == 401
        assert c.post("/ringg/tools/list_brands", json={}, headers={"X-Ringg-Tools-Key": "key-a", "X-Ringg-Agent-Id": "agent-a", "Store-UUID": "disabled-store"}).status_code == 403
        assert c.post("/ringg/tools/list_brands", json={}, headers={"X-Ringg-Tools-Key": "key-a", "X-Ringg-Agent-Id": "agent-a", "Store-UUID": "missing-store"}).status_code == 403
        assert len(calls) == 1
    finally:
        stores.reset()



# ---- patch 10: an agent mapped to "*" serves any store that has an enabled settings file ----------------------------------

WILD = {AGENT: ringg.WILDCARD}


def test_wildcard_agent_setting_is_accepted_only_as_the_whole_value(monkeypatch):
    monkeypatch.setenv("RINGG_AGENT_STORES", json.dumps({AGENT: "*", "agent-b": [STORE], "agent-c": "store-c"}))
    assert ringg.agent_stores_from_env() == {AGENT: "*", "agent-b": [STORE], "agent-c": "store-c"}
    for bad in ({AGENT: ["*"]}, {AGENT: [STORE, "*"]}, {AGENT: "**"}, {AGENT: "*a"}, {"bad id": "*"}, {AGENT: ""}, {AGENT: []}):
        monkeypatch.setenv("RINGG_AGENT_STORES", json.dumps(bad))
        with pytest.raises(ValueError):
            ringg.agent_stores_from_env()


def test_a_wildcard_agent_must_name_a_well_formed_store_and_has_no_default():
    assert ringg.is_wildcard(WILD, AGENT) and not ringg.is_wildcard({AGENT: STORE}, AGENT) and not ringg.is_wildcard(WILD, None)
    assert ringg.resolve_store(AGENT, WILD, "any-store-7") == "any-store-7"
    assert ringg.resolve_store(AGENT, WILD, "  any-store-7 ") == "any-store-7"
    for none_named in (None, "", "null", "any", "*", "  ", 5):
        assert ringg.resolve_store(AGENT, WILD, none_named) is None                     # no guess, and "*" is not a store
    for malformed in ("../etc/passwd", "a b", "x.y", "a/b", "s" * 65, "store;drop"):
        assert ringg.resolve_store(AGENT, WILD, malformed) is None
    assert ringg.resolve_store("unlisted", WILD, "any-store-7") is None                  # only the listed agent is a wildcard
    assert ringg.resolve_store(None, WILD, "any-store-7") is None


def test_a_star_inside_a_list_is_never_a_wildcard():
    assert ringg.resolve_store(AGENT, {AGENT: ["*"]}, "store-x") is None
    assert ringg.resolve_store(AGENT, {AGENT: [STORE, "*"]}, "store-x") is None
    assert ringg.resolve_store(AGENT, {AGENT: [STORE, "*"]}, STORE) == STORE


def test_the_other_agents_stay_limited_next_to_a_wildcard_agent():
    mixed = {AGENT: "*", "agent-b": [STORE]}
    assert ringg.resolve_store("agent-b", mixed, "store-x") is None
    assert ringg.resolve_store("agent-b", mixed, None) == STORE
    assert ringg.resolve_store(AGENT, mixed, "store-x") == "store-x"


def test_handle_drops_an_event_when_the_settings_check_says_no_and_does_not_mark_it_seen():
    seen = ringg.Seen()
    event = {**COMPLETED, "custom_args_values": {"store_uuid": "store-x", "conversation_id": "c1"}}
    assert ringg.handle(event, WILD, seen, store_served=lambda store, wildcard: False) == ("store_not_configured", None)
    what, ev = ringg.handle(event, WILD, seen, store_served=lambda store, wildcard: store == "store-x" and wildcard)
    assert what == "recorded" and ev["store"] == "store-x"                              # the refusal did not use up the (call, event) pair
    assert ringg.handle(event, WILD, ringg.Seen()) [0] == "recorded"                    # no check given: as before


def _settings_dir(tmp_path, monkeypatch, **extra):
    from bot import stores
    files = {STORE: {"store_name": "Store A", "enabled": True}, "store-b": {"store_name": "Store B", "enabled": True},
             "store-off": {"store_name": "Off", "enabled": False}, **extra}
    for name, data in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    monkeypatch.setenv("BOT_STORE_CONFIG_DIR", str(tmp_path))
    stores.reset()
    return stores


def test_http_wildcard_tool_serves_configured_stores_only(monkeypatch, tmp_path):
    stores = _settings_dir(tmp_path, monkeypatch)
    try:
        calls = []
        c, sink = _tools_client(calls, stores=WILD)
        ok = c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "store-b"})
        assert ok.status_code == 200 and calls[-1][2].store_uuid == "store-b" and sink.rows[-1]["store"] == "store-b"
        assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "X-Store-Uuid": STORE}).status_code == 200
        n = len(calls)
        for header in ({"Store-UUID": "store-off"}, {"Store-UUID": "no-such-store"}, {"Store-UUID": "../x"}, {"Store-UUID": "*"},
                       {"Store-UUID": "null"}, {}):
            assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, **header}).status_code == 403, header
        assert c.post("/ringg/tools/list_brands", json={"store_uuid": "store-b"}, headers=TH).status_code == 403   # the body never names the store
        assert len(calls) == n                                                                                    # no tool ran for any refusal
    finally:
        stores.reset()


def test_a_new_store_needs_only_its_settings_file(monkeypatch, tmp_path):
    stores = _settings_dir(tmp_path, monkeypatch)
    try:
        calls = []
        c, _ = _tools_client(calls, stores=WILD)
        assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "brand-new"}).status_code == 403
        stores.reset()                                                                                           # skip the 60 s settings cache
        (tmp_path / "brand-new.json").write_text(json.dumps({"store_name": "Brand New", "enabled": True}))
        assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": "brand-new"}).status_code == 200
        assert calls[-1][2].store_uuid == "brand-new"
    finally:
        stores.reset()


def test_http_wildcard_without_a_settings_folder_refuses_everything(monkeypatch):
    from bot import stores
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)
    stores.reset()
    calls = []
    c, _ = _tools_client(calls, stores=WILD)
    for store in (STORE, "store-b", "anything"):
        assert c.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": store}).status_code == 403
    assert calls == []
    c2, _ = _tools_client(calls, stores={AGENT: [STORE]})                                                        # a listed agent still works in development
    assert c2.post("/ringg/tools/list_brands", json={}, headers={**TH, "Store-UUID": STORE}).status_code == 200


def test_http_wildcard_agent_still_needs_its_own_key_and_the_listed_agent_cannot_roam(monkeypatch, tmp_path):
    stores = _settings_dir(tmp_path, monkeypatch)
    try:
        calls = []
        mixed = {"agent-wild": "*", "agent-fixed": [STORE]}
        keys = {"agent-wild": "key-w", "agent-fixed": "key-f"}
        c, _ = _tools_client(calls, stores=mixed, key=keys)

        def post(agent, key, store):
            return c.post("/ringg/tools/list_brands", json={}, headers={"X-Ringg-Tools-Key": key, "X-Ringg-Agent-Id": agent, "Store-UUID": store})

        assert post("agent-wild", "key-w", "store-b").status_code == 200
        assert post("agent-wild", "key-f", "store-b").status_code == 401                 # the other agent's key does not open it
        assert post("agent-fixed", "key-f", "store-b").status_code == 403                # configured, but not this agent's store
        assert post("agent-fixed", "key-f", STORE).status_code == 200
    finally:
        stores.reset()


def test_http_webhook_records_events_only_for_configured_stores(monkeypatch, tmp_path):
    stores = _settings_dir(tmp_path, monkeypatch)
    try:
        c, sink = _client(stores=WILD)
        h = {"X-Webhook-Secret": "whsec"}

        def event(store, call):
            custom = {"conversation_id": "c1", **({"store_uuid": store} if store else {})}
            return {**COMPLETED, "call_id": call, "custom_args_values": custom}

        for store, call in (("store-b", "call-ok"), ("store-off", "call-1"), ("no-such-store", "call-2"), (None, "call-3"), ("../x", "call-4")):
            assert c.post("/webhooks/ringg", json=event(store, call), headers=h).status_code == 200     # always acknowledged
        assert [(r["store"], r["call_id"]) for r in sink.rows] == [("store-b", "call-ok")]
    finally:
        stores.reset()


def test_http_webhook_also_drops_events_of_a_switched_off_store_for_a_listed_agent(monkeypatch, tmp_path):
    stores = _settings_dir(tmp_path, monkeypatch)
    try:
        c, sink = _client(stores={AGENT: [STORE, "store-off"]})
        h = {"X-Webhook-Secret": "whsec"}
        off = {**COMPLETED, "call_id": "call-off", "custom_args_values": {"store_uuid": "store-off"}}
        assert c.post("/webhooks/ringg", json=off, headers=h).status_code == 200
        assert sink.rows == []
        on = {**COMPLETED, "call_id": "call-on", "custom_args_values": {"store_uuid": STORE}}
        assert c.post("/webhooks/ringg", json=on, headers=h).status_code == 200
        assert [r["store"] for r in sink.rows] == [STORE]
    finally:
        stores.reset()


def test_http_webhook_wildcard_without_a_settings_folder_records_nothing(monkeypatch):
    from bot import stores
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)
    stores.reset()
    c, sink = _client(stores=WILD)
    ev = {**COMPLETED, "custom_args_values": {"store_uuid": STORE}}
    assert c.post("/webhooks/ringg", json=ev, headers={"X-Webhook-Secret": "whsec"}).status_code == 200
    assert sink.rows == []

"""The HTTP service: the store and the customer come only from trusted headers, never from the body."""
import json

import pytest
from fastapi.testclient import TestClient

from bot import stores
from bot.server import Limits, create_app

@pytest.fixture(autouse=True)
def dev_mode(monkeypatch):
    """These tests are about headers and bodies, not store settings: run them the way a developer does locally."""
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("BOT_ALLOW_DEV_DEFAULTS", "1")
    stores.reset()
    yield
    stores.reset()


KEY = "s3cret-key"
GOOD = {"X-Bot-Key": KEY, "Store-UUID": "store-a", "X-Client-Id": "203.0.113.7"}


class Spy:
    def __init__(self, out=None, boom=False):
        self.calls, self.out, self.boom = [], out or {"reply": "hi", "tools_used": ["x"], "blocked": None}, boom

    def __call__(self, message, llm, history, ctx=None):
        self.calls.append((message, llm, history, ctx))
        if self.boom:
            raise RuntimeError("model down with secret details")
        return self.out


def client(spy=None, key=KEY, limits=None):
    spy = spy or Spy()
    return TestClient(create_app(answer_fn=spy, llm_factory=lambda: "LLM", service_key=key, limits=limits or Limits())), spy


def test_chat_passes_the_trusted_context_and_returns_only_reply_and_blocked():
    c, spy = client()
    r = c.post("/chat", json={"message": "hello", "history": [{"role": "user", "text": "a"}]}, headers={**GOOD, "X-Customer-Uuid": "cust-9"})
    assert r.status_code == 200 and r.json() == {"reply": "hi", "blocked": None, "products": [], "display": None}      # tools_used is not exposed
    message, llm, history, ctx = spy.calls[0]
    assert (message, llm, history) == ("hello", "LLM", [{"role": "user", "text": "a"}])
    assert (ctx.store_uuid, ctx.client_id, ctx.customer_uuid) == ("store-a", "203.0.113.7", "cust-9")


def test_a_visitor_has_no_customer():
    c, spy = client()
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert spy.calls[0][3].customer_uuid is None


@pytest.mark.parametrize("extra", [{"store_uuid": "store-b"}, {"customer_uuid": "c1"}, {"logged_in": True}, {"ctx": {}}])
def test_the_body_cannot_name_a_store_a_customer_or_a_login_flag(extra):
    c, spy = client()
    r = c.post("/chat", json={"message": "hello", **extra}, headers=GOOD)
    assert r.status_code == 422 and spy.calls == []


@pytest.mark.parametrize("headers", [
    {k: v for k, v in GOOD.items() if k != "X-Bot-Key"},
    {**GOOD, "X-Bot-Key": "wrong"},
    {**GOOD, "X-Bot-Key": ""},
])
def test_a_missing_or_wrong_service_key_is_refused_before_anything_runs(headers):
    c, spy = client()
    assert c.post("/chat", json={"message": "hello"}, headers=headers).status_code == 401 and spy.calls == []


def test_without_a_configured_key_every_chat_is_refused(monkeypatch):
    monkeypatch.delenv("BOT_SERVICE_KEY", raising=False)
    for key in ("", None):                                   # empty, or unset in the environment
        c, spy = client(key=key)
        assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 503 and spy.calls == []


def test_the_key_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", KEY)
    c, spy = client(key=None)
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


@pytest.mark.parametrize("headers", [
    {k: v for k, v in GOOD.items() if k != "Store-UUID"},
    {**GOOD, "Store-UUID": "bad store!"},
    {k: v for k, v in GOOD.items() if k != "X-Client-Id"},
    {**GOOD, "X-Client-Id": "x" * 200},
    {**GOOD, "X-Customer-Uuid": "bad id\r\nX: y"},
])
def test_bad_trusted_headers_are_400(headers):
    c, spy = client()
    assert c.post("/chat", json={"message": "hello"}, headers=headers).status_code in (400, 422) and spy.calls == []


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "x" * 1001}, {"message": "hi", "history": [{"role": "system", "text": "x"}]},
                                  {"message": "hi", "history": [{"role": "user", "text": "x"}] * 11}])
def test_message_and_history_limits(body):
    c, spy = client()
    assert c.post("/chat", json=body, headers=GOOD).status_code == 422 and spy.calls == []


def test_a_crash_returns_a_generic_502_without_details():
    c, _ = client(Spy(boom=True))
    r = c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert r.status_code == 502 and "secret" not in r.text


def test_health_needs_no_key_and_docs_are_off():
    c, _ = client()
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


# ---------- store settings must be configured ----------

def test_without_store_settings_the_service_refuses_unless_dev_mode_was_chosen(monkeypatch):
    monkeypatch.delenv("BOT_ALLOW_DEV_DEFAULTS", raising=False)
    stores.reset()
    c, spy = client()
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 503 and spy.calls == []


def test_with_a_store_settings_folder_chats_are_served(monkeypatch, tmp_path):
    (tmp_path / "store-a.json").write_text(json.dumps({"store_name": "A", "allowed_domains": ["a.com"]}))
    monkeypatch.delenv("BOT_ALLOW_DEV_DEFAULTS", raising=False)
    monkeypatch.setenv("BOT_STORE_CONFIG_DIR", str(tmp_path))
    stores.reset()
    c, spy = client()
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200 and len(spy.calls) == 1


def test_a_wrong_key_is_401_even_when_store_settings_are_missing(monkeypatch):
    monkeypatch.delenv("BOT_ALLOW_DEV_DEFAULTS", raising=False)
    stores.reset()
    c, _ = client()
    assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Bot-Key": "wrong"}).status_code == 401


# ---------- rate limits ----------

def test_one_visitor_is_limited_and_told_when_to_retry():
    c, spy = client(limits=Limits(per_client=2, per_store=100))
    for _ in range(2):
        assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200
    r = c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and len(spy.calls) == 2


def test_another_visitor_in_the_same_store_is_not_limited_by_the_first():
    c, _ = client(limits=Limits(per_client=1, per_store=100))
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 429
    assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Client-Id": "198.51.100.9"}).status_code == 200


def test_the_same_visitor_id_in_another_store_has_its_own_count():
    c, _ = client(limits=Limits(per_client=1, per_store=100))
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200
    assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "Store-UUID": "store-b"}).status_code == 200


def test_a_store_wide_limit_applies_across_visitors():
    c, _ = client(limits=Limits(per_client=100, per_store=2))
    for i in range(2):
        assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Client-Id": f"10.0.0.{i}"}).status_code == 200
    assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Client-Id": "10.0.0.9"}).status_code == 429
    assert c.post("/chat", json={"message": "hello"}, headers={**GOOD, "Store-UUID": "store-b"}).status_code == 200


def test_refused_requests_do_not_use_up_the_limit():
    c, _ = client(limits=Limits(per_client=1, per_store=100))
    assert c.post("/chat", json={"message": ""}, headers=GOOD).status_code == 422           # invalid body
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


# ---------- concurrency ----------

def test_when_all_slots_are_busy_the_chat_is_refused_with_503():
    c, spy = client(limits=Limits(max_concurrent=1))
    assert c.app.state.gate.try_enter()
    r = c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert r.status_code == 503 and r.headers["Retry-After"] == "2" and spy.calls == []
    c.app.state.gate.leave()
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


def test_a_crash_gives_the_slot_back():
    c, _ = client(Spy(boom=True), limits=Limits(max_concurrent=1))
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 502
    assert c.app.state.gate.try_enter()                                                      # free again


# ---------- body size ----------

def test_a_declared_body_over_the_limit_is_413_before_it_is_read():
    c, spy = client(limits=Limits(max_body_bytes=200))
    r = c.post("/chat", content=json.dumps({"message": "x" * 500}), headers={**GOOD, "Content-Type": "application/json"})
    assert r.status_code == 413 and spy.calls == []


def test_a_chunked_body_over_the_limit_is_413_too():
    c, spy = client(limits=Limits(max_body_bytes=200))

    def chunks():
        for _ in range(10):
            yield b'{"message": "' + b"x" * 40 + b'"}'

    r = c.post("/chat", content=chunks(), headers={**GOOD, "Content-Type": "application/json"})
    assert r.status_code == 413 and spy.calls == []


def test_a_normal_body_is_unaffected_by_the_limit():
    c, _ = client(limits=Limits(max_body_bytes=2000))
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


# ---------- limits settings ----------

def test_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("BOT_RATE_PER_CLIENT", "5")
    monkeypatch.setenv("BOT_MAX_CONCURRENT", "3")
    lim = Limits.from_env()
    assert (lim.per_client, lim.per_store, lim.max_concurrent, lim.max_body_bytes) == (5, 300, 3, 16384)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
def test_bad_limit_settings_stop_the_service_from_starting(monkeypatch, bad):
    monkeypatch.setenv("BOT_RATE_PER_STORE", bad)
    with pytest.raises(ValueError):
        Limits.from_env()


def test_the_model_client_is_built_once():
    built = []
    spy = Spy()
    app = create_app(answer_fn=spy, llm_factory=lambda: built.append(1) or "LLM", service_key=KEY, limits=Limits())
    c = TestClient(app)
    for _ in range(3):
        c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Client-Id": f"c{len(built)}{_}"})
    assert built == [1]


# ---------- access log ----------
import logging
import re


def lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "bot.access"]


def fields(line):
    return dict(part.split("=", 1) for part in line.split()[1:])


SECRET_MESSAGE = "my private question about order 4417"
SECRET_REPLY = "private reply text"
CUSTOMER = "cust-secret-77"


def test_a_chat_writes_one_line_with_the_facts_and_nothing_private(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client(Spy(out={"reply": SECRET_REPLY, "tools_used": ["search_products", "get_store_info"], "blocked": None}))
    r = c.post("/chat", json={"message": SECRET_MESSAGE, "history": [{"role": "user", "text": "earlier words"}]},
               headers={**GOOD, "X-Customer-Uuid": CUSTOMER})
    assert r.status_code == 200
    out = lines(caplog)
    assert len(out) == 1
    f = fields(out[0])
    assert f["status"] == "200" and f["store"] == "store-a" and f["logged_in"] == "yes" and f["blocked"] == "-"
    assert f["tools"] == "search_products,get_store_info" and int(f["ms"]) >= 0
    assert re.fullmatch(r"[0-9a-f]{12}", f["client"])                       # a hash, never the address
    for private in (SECRET_MESSAGE, SECRET_REPLY, CUSTOMER, "203.0.113.7", "earlier words", KEY):
        assert private not in out[0]
    assert f["request_id"] == r.headers["X-Request-Id"] and re.fullmatch(r"[0-9a-f]{16}", f["request_id"])


def test_the_client_hash_is_the_same_short_hash_tracing_uses(caplog):
    """bot/tracing.py tags a visitor with sha256(client_id)[:12]; the log line must carry exactly that value, so a trace and
    a log line about the same visitor can be matched."""
    import hashlib
    caplog.set_level(logging.INFO)
    c, _ = client()
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert fields(lines(caplog)[0])["client"] == hashlib.sha256(b"203.0.113.7").hexdigest()[:12]


def test_a_visitor_is_logged_as_not_logged_in(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client()
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert fields(lines(caplog)[0])["logged_in"] == "no"


def test_a_blocked_reply_is_logged_with_its_reason(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client(Spy(out={"reply": "no", "tools_used": [], "blocked": "price_unverified"}))
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    f = fields(lines(caplog)[0])
    assert f["blocked"] == "price_unverified" and f["tools"] == "-"


@pytest.mark.parametrize("name,headers,status", [
    ("no key", {k: v for k, v in GOOD.items() if k != "X-Bot-Key"}, 401),
    ("wrong key", {**GOOD, "X-Bot-Key": "wrong-key-value"}, 401),
    ("bad store", {**GOOD, "Store-UUID": "bad store!"}, 400),
])
def test_refused_requests_are_logged_without_trusting_their_headers(caplog, name, headers, status):
    caplog.set_level(logging.INFO)
    c, spy = client()
    assert c.post("/chat", json={"message": "hello"}, headers=headers).status_code == status
    out = lines(caplog)
    assert len(out) == 1 and fields(out[0])["status"] == str(status) and spy.calls == []
    if status == 401:
        assert fields(out[0])["store"] == "-" and "wrong-key-value" not in out[0]      # an unauthenticated header is not logged


def test_limits_and_crashes_are_logged_too(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client(limits=Limits(per_client=1, per_store=100, max_concurrent=1))
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    c.post("/chat", json={"message": "hello"}, headers=GOOD)                              # 429
    assert c.app.state.gate.try_enter()
    c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Client-Id": "other"})  # 503 busy
    c.app.state.gate.leave()
    boom, _ = client(Spy(boom=True))
    boom.post("/chat", json={"message": "hello"}, headers=GOOD)                           # 502
    assert [fields(x)["status"] for x in lines(caplog)] == ["200", "429", "503", "502"]
    assert not any("secret details" in x for x in lines(caplog))                          # the crash text is not in the access line


def test_a_too_large_body_is_logged(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client(limits=Limits(max_body_bytes=100))
    c.post("/chat", content=json.dumps({"message": "x" * 300}), headers={**GOOD, "Content-Type": "application/json"})
    assert fields(lines(caplog)[0])["status"] == "413"


def test_warnings_for_refusals_and_failures_info_for_the_rest(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client()
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Bot-Key": "wrong-key-value"})
    levels = [r.levelname for r in caplog.records if r.name == "bot.access"]
    assert levels == ["INFO", "WARNING"]


def test_health_and_other_paths_are_not_logged(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client()
    c.get("/health")
    c.get("/nothing-here")
    assert lines(caplog) == []


def test_the_callers_request_id_is_used_when_it_is_safe(caplog):
    caplog.set_level(logging.INFO)
    c, _ = client()
    r = c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Request-Id": "mw-abc_123.9"})
    assert r.headers["X-Request-Id"] == "mw-abc_123.9" and fields(lines(caplog)[0])["request_id"] == "mw-abc_123.9"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", 'quote"id', "a=b"])
def test_an_unsafe_request_id_is_replaced(caplog, bad):
    caplog.set_level(logging.INFO)
    c, _ = client()
    r = c.post("/chat", json={"message": "hello"}, headers={**GOOD, "X-Request-Id": bad})
    assert re.fullmatch(r"[0-9a-f]{16}", r.headers["X-Request-Id"]) and len(lines(caplog)) == 1


def test_tool_names_and_reasons_cannot_forge_or_split_a_log_line(caplog):
    caplog.set_level(logging.INFO)
    evil = {"reply": "ok", "tools_used": ["search_products", "x status=200\nFAKE", "Bad-Name", 5, None],
            "blocked": "reason with spaces\nstatus=999 store=other"}
    c, _ = client(Spy(out=evil))
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    out = lines(caplog)
    assert len(out) == 1 and "\n" not in out[0] and out[0].count("status=") == 1 and "store=other" not in out[0].replace("?", "")
    f = fields(out[0])
    assert f["tools"] == "search_products" and "\n" not in f["blocked"]


def test_a_failing_logger_never_breaks_a_chat(monkeypatch):
    import bot.server as server

    def broken(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(server.access_log, "log", broken)
    c, _ = client()
    assert c.post("/chat", json={"message": "hello"}, headers=GOOD).status_code == 200


def test_the_access_line_is_visible_without_any_logging_setup(capsys, monkeypatch):
    """uvicorn configures only its own loggers; the app attaches a handler when nothing else did."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(server_access_log(), "handlers", [])
    c, _ = client()
    c.post("/chat", json={"message": "hello"}, headers=GOOD)
    assert "chat request_id=" in capsys.readouterr().err


def server_access_log():
    import bot.server as server
    return server.access_log


# ---------- the customer's login token ----------
TOKEN = "eyJhbGciOiJIUzI1NiJ9.SECRET-PAYLOAD.SECRET-SIGNATURE"


def test_the_customer_token_reaches_the_chat_only_from_the_trusted_header():
    c, spy = client()
    r = c.post("/chat", json={"message": "where is my order"}, headers={**GOOD, "X-Customer-Token": TOKEN})
    assert r.status_code == 200
    ctx = spy.calls[0][3]
    assert ctx.customer_token == TOKEN and ctx.customer_uuid is None
    assert TOKEN not in r.text and TOKEN not in repr(ctx)


def test_a_visitor_has_no_token_and_the_body_cannot_supply_one():
    c, spy = client()
    c.post("/chat", json={"message": "hi"}, headers=GOOD)
    assert spy.calls[0][3].customer_token is None
    r = c.post("/chat", json={"message": "hi", "customer_token": TOKEN}, headers=GOOD)
    assert r.status_code == 422 and len(spy.calls) == 1


@pytest.mark.parametrize("bad", ["short", "has space in it 1234567890", "line\r\nbreak-1234567890", "x" * 4097, "tok;en=1234567890123"])
def test_a_malformed_token_is_refused_and_never_echoed(bad):
    c, spy = client()
    try:
        r = c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Customer-Token": bad})
    except Exception:                      # the test client itself may refuse a header with a line break
        return
    assert r.status_code == 400 and bad not in r.text and spy.calls == []


def test_a_token_marks_the_chat_as_logged_in_in_the_access_log(caplog):
    caplog.set_level("INFO", logger="bot.access")
    c, _ = client()
    c.post("/chat", json={"message": "secret question"}, headers={**GOOD, "X-Customer-Token": TOKEN})
    line = next(r.getMessage() for r in caplog.records if r.name == "bot.access")
    assert "logged_in=yes" in line and "SECRET" not in caplog.text and "secret question" not in caplog.text


def test_the_service_key_is_still_checked_before_the_token_is_looked_at():
    c, spy = client()
    r = c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Bot-Key": "wrong", "X-Customer-Token": TOKEN})
    assert r.status_code == 401 and spy.calls == [] and TOKEN not in r.text


def test_per_store_service_keys_isolate_stores_and_reject_cross_store_keys():
    c, spy = client(key={"store-a": "key-a", "store-b": "key-b"})
    assert c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Bot-Key": "key-a", "Store-UUID": "store-a"}).status_code == 200
    assert c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Bot-Key": "key-a", "Store-UUID": "store-b"}).status_code == 401
    assert c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Bot-Key": "key-b", "Store-UUID": "store-b"}).status_code == 200
    assert c.post("/chat", json={"message": "hi"}, headers={**GOOD, "X-Bot-Key": "key-a", "Store-UUID": "store-c"}).status_code == 401
    assert len(spy.calls) == 2


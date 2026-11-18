"""The HTTP service: the store and the customer come only from trusted headers, never from the body."""
import pytest
from fastapi.testclient import TestClient

from bot.server import create_app

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


def client(spy=None, key=KEY):
    spy = spy or Spy()
    return TestClient(create_app(answer_fn=spy, llm_factory=lambda: "LLM", service_key=key)), spy


def test_chat_passes_the_trusted_context_and_returns_only_reply_and_blocked():
    c, spy = client()
    r = c.post("/chat", json={"message": "hello", "history": [{"role": "user", "text": "a"}]}, headers={**GOOD, "X-Customer-Uuid": "cust-9"})
    assert r.status_code == 200 and r.json() == {"reply": "hi", "blocked": None}      # tools_used is not exposed
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

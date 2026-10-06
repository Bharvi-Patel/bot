"""Tracing is optional: off by default, masks personal data, and can never break the bot."""
import pytest

from sjbot import guardrails as g
from sjbot import router, tracing
from sjbot.context import ChatContext
from sjbot.llm_base import LLMTurn, ToolCall


class FakeObs:
    def __init__(self, rec):
        self.rec = rec

    def update(self, **kw):
        self.rec["updates"].append(kw)


class FakeCM:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return FakeObs(self.rec)

    def __exit__(self, et, ev, tb):
        self.rec["exit"] = et


class FakeClient:
    def __init__(self):
        self.spans = []

    def start_as_current_observation(self, **kw):
        rec = {"start": kw, "updates": [], "exit": "open"}
        self.spans.append(rec)
        return FakeCM(rec)


class FakeLLM:
    model, tokens = "fake", 0

    def __init__(self, *turns):
        self.turns = list(turns)

    def generate(self, messages):
        self.tokens += 1200
        return self.turns.pop(0)


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(tracing, "_client", c)
    return c


# ---------- off by default ----------
def test_tracing_is_off_in_tests_and_everything_is_a_no_op():
    assert tracing.enabled() is False
    with tracing.span("x", input={"a": 1}) as sp:
        sp.update(output="y")
    with tracing.visitor(ChatContext(client_id="1.2.3.4")):
        pass
    llm = FakeLLM(LLMTurn(text="hi"))
    assert tracing.generate(llm, [{"role": "user", "text": "hello"}]).text == "hi"
    tracing.flush()


def test_router_behaves_the_same_with_tracing_off(monkeypatch):
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", "{}")
    out = router.answer("what brands do you carry", FakeLLM(LLMTurn(text="We carry several brands.")))
    assert out["blocked"] is None and out["reply"] == "We carry several brands."


# ---------- masking ----------
def test_mask_removes_emails_phones_and_long_numbers_but_keeps_ordinary_text():
    text = "order 102500001 for jo@example.com, call (731) 423-6565 or 731-423-6565. SKU 6310138 costs $257.46"
    out = tracing.mask(text)
    assert "jo@example.com" not in out and "102500001" not in out and "423-6565" not in out
    assert "[email]" in out and "[number]" in out and "[phone]" in out
    assert "6310138" in out and "$257.46" in out                      # short SKUs and prices stay readable


def test_mask_walks_nested_data_and_redacts_secret_keys():
    data = {"email": "a@b.co", "args": [{"order_number": "102500001"}], "n": 5, "ok": True, "customer_uuid": "abc", "empty": None}
    assert tracing.mask(data) == {"email": "[redacted]", "args": [{"order_number": "[number]"}], "n": 5, "ok": True,
                                  "customer_uuid": "[redacted]", "empty": None}


# ---------- spans ----------
def test_span_records_input_output_and_closes_cleanly(client):
    with tracing.span("tool:x", input={"a": 1}) as sp:
        sp.update(output={"b": 2})
    rec = client.spans[0]
    assert rec["start"]["name"] == "tool:x" and rec["start"]["input"] == {"a": 1}
    assert rec["updates"] == [{"output": {"b": 2}}] and rec["exit"] is None


def test_span_passes_exceptions_through_and_tells_the_trace(client):
    with pytest.raises(ValueError):
        with tracing.span("boom"):
            raise ValueError("x")
    assert client.spans[0]["exit"] is ValueError


def test_a_failing_trace_client_never_breaks_the_bot(monkeypatch):
    class Broken:
        def start_as_current_observation(self, **kw):
            raise RuntimeError("server down")
    monkeypatch.setattr(tracing, "_client", Broken())
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", "{}")
    with tracing.span("x") as sp:
        sp.update(output=1)
    assert tracing.generate(FakeLLM(LLMTurn(text="ok")), [{"role": "user", "text": "hi"}]).text == "ok"
    out = router.answer("what brands do you carry", FakeLLM(LLMTurn(text="We carry several brands.")))
    assert out["blocked"] is None and out["reply"] == "We carry several brands."


def test_generation_span_has_readable_input_output_and_token_usage(client):
    llm = FakeLLM(LLMTurn(text="", tool_calls=[ToolCall("list_brands", {})], raw=object()))
    msgs = [{"role": "user", "text": "brands?"}, {"role": "assistant", "text": "", "tool_calls": [ToolCall("a", {"k": 1})], "raw": object()}]
    tracing.generate(llm, msgs)
    rec = client.spans[0]
    assert rec["start"]["as_type"] == "generation" and rec["start"]["model"] == "fake"
    assert rec["start"]["input"] == [{"role": "user", "text": "brands?"},
                                     {"role": "assistant", "tool_calls": [{"name": "a", "args": {"k": 1}}]}]     # no provider objects
    update = rec["updates"][0]
    assert update["output"]["tool_calls"] == [{"name": "list_brands", "args": {}}] and update["usage_details"] == {"total": 1200}


# ---------- the whole turn ----------
def test_a_turn_produces_chat_turn_llm_tool_and_reply_check_spans(client, monkeypatch):
    monkeypatch.setitem(g.TOOLS, "list_brands", lambda a: {"brands": ["Acme"]})
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", "{}")
    llm = FakeLLM(LLMTurn(tool_calls=[ToolCall("list_brands", {})]), LLMTurn(text="We carry Acme."))
    out = router.answer("what brands do you carry", llm)
    assert out["blocked"] is None
    names = [s["start"]["name"] for s in client.spans]
    assert names == ["chat_turn", "llm", "tool:list_brands", "llm", "reply_check"]
    turn = client.spans[0]
    assert turn["start"]["input"] == "what brands do you carry"
    assert turn["updates"][0]["output"] == {"reply": "We carry Acme.", "tools_used": ["list_brands"], "blocked": None}
    assert client.spans[2]["updates"][0]["output"] == {"brands": ["Acme"]}
    assert client.spans[4]["updates"][0]["output"] == {"ok": True, "reason": None}


def test_the_llm_object_is_never_part_of_a_trace(client, monkeypatch):
    monkeypatch.setattr(g, "_STORE_CONTACT_TEXT", "{}")
    router.answer("what brands do you carry", FakeLLM(LLMTurn(text="We carry several brands.")))
    assert "FakeLLM" not in str([s["start"] for s in client.spans])
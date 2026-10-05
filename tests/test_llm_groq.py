"""GroqChat without the network: a fake client records what we send and replays canned responses."""
import json
from types import SimpleNamespace as NS

from sjbot.llm_base import ToolCall
from sjbot.llm_groq import GroqChat
from sjbot.tool_schemas import TOOL_DECLARATIONS


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.sent = list(responses), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def reply(content="", calls=()):
    tcs = [NS(id=i, function=NS(name=n, arguments=a)) for i, n, a in calls] or None
    return NS(choices=[NS(message=NS(content=content, tool_calls=tcs))])


def test_tool_call_roundtrip_keeps_ids_in_order():
    fake = FakeClient(reply(calls=[("id1", "list_brands", "{}"), ("id2", "get_store_info", "")]), reply("done"))
    llm = GroqChat(client=fake)
    turn = llm.generate([{"role": "user", "text": "hi"}])
    assert [c.name for c in turn.tool_calls] == ["list_brands", "get_store_info"] and turn.tool_calls[1].args == {}
    history = [{"role": "user", "text": "hi"},
               {"role": "assistant", "text": "", "tool_calls": turn.tool_calls, "raw": turn.raw},
               {"role": "tool", "name": "list_brands", "result": {"count": 19}},
               {"role": "tool", "name": "get_store_info", "result": {"phone": "555"}}]
    assert llm.generate(history).text == "done"
    sent = fake.sent[1]["messages"]
    assert sent[0]["role"] == "system"
    assert [m["tool_call_id"] for m in sent if m["role"] == "tool"] == ["id1", "id2"]
    assert json.loads(sent[3]["content"]) == {"count": 19}


def test_bad_arguments_become_empty_dict():
    llm = GroqChat(client=FakeClient(reply(calls=[("a", "list_brands", "not json")])))
    assert llm.generate([{"role": "user", "text": "x"}]).tool_calls[0].args == {}


def test_retries_once_on_tool_use_failed():
    fake = FakeClient(Exception("400 tool_use_failed"), reply("ok"))
    assert GroqChat(client=fake).generate([{"role": "user", "text": "x"}]).text == "ok"
    assert len(fake.sent) == 2


def test_other_errors_propagate():
    import pytest
    with pytest.raises(Exception):
        GroqChat(client=FakeClient(Exception("429 rate limit"))).generate([{"role": "user", "text": "x"}])


def test_every_tool_is_declared_with_a_schema():
    tools = GroqChat(client=FakeClient())._tools
    assert [t["function"]["name"] for t in tools] == [d["name"] for d in TOOL_DECLARATIONS]
    assert all(t["function"]["parameters"]["type"] == "object" for t in tools)


def test_assistant_message_without_raw_still_gets_ids():
    llm = GroqChat(client=FakeClient())
    msgs = llm._messages([{"role": "user", "text": "x"},
                          {"role": "assistant", "text": "", "tool_calls": [ToolCall("list_brands", {})], "raw": None},
                          {"role": "tool", "name": "list_brands", "result": {}}])
    assert msgs[2]["tool_calls"][0]["id"] == msgs[3]["tool_call_id"]


def test_null_arguments_are_dropped():
    llm = GroqChat(client=FakeClient(reply(calls=[("a", "get_shipping_options", '{"zone_keyword": null}')])))
    assert llm.generate([{"role": "user", "text": "x"}]).tool_calls[0].args == {}


def test_retry_after_tool_use_failed_tells_the_model_why():
    fake = FakeClient(Exception("400 tool_use_failed"), reply("ok"))
    GroqChat(client=fake).generate([{"role": "user", "text": "x"}])
    assert "rejected" in fake.sent[1]["messages"][-1]["content"]


def test_reasoning_effort_only_for_gpt_oss(monkeypatch):
    monkeypatch.delenv("SJ_REASONING_EFFORT", raising=False)
    fake = FakeClient(reply("a"), reply("b"))
    GroqChat(model="openai/gpt-oss-120b", client=fake).generate([{"role": "user", "text": "x"}])
    GroqChat(model="llama-3.3-70b-versatile", client=fake).generate([{"role": "user", "text": "x"}])
    assert fake.sent[0]["reasoning_effort"] == "low" and "reasoning_effort" not in fake.sent[1]


def test_reasoning_effort_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SJ_REASONING_EFFORT", "")
    fake = FakeClient(reply("a"))
    GroqChat(model="openai/gpt-oss-120b", client=fake).generate([{"role": "user", "text": "x"}])
    assert "reasoning_effort" not in fake.sent[0]


def test_tokens_are_counted():
    r = reply("a")
    r.usage = NS(total_tokens=1234)
    llm = GroqChat(client=FakeClient(r))
    llm.generate([{"role": "user", "text": "x"}])
    assert llm.tokens == 1234


def _client_with_models(ids=None, error=None):
    c = FakeClient()

    def list_models():
        if error:
            raise error
        return NS(data=[NS(id=i) for i in ids])
    c.models = NS(list=list_models)
    return c


def test_ensure_model_lists_alternatives_when_model_is_missing():
    import pytest
    with pytest.raises(SystemExit) as e:
        GroqChat(model="gone", client=_client_with_models(["a-model", "b/c-model"])).ensure_model()
    assert "gone" in str(e.value) and "b/c-model" in str(e.value) and "SJ_LLM_MODEL" in str(e.value)


def test_ensure_model_accepts_ids_with_a_slash():
    GroqChat(model="openai/gpt-oss-20b", client=_client_with_models(["openai/gpt-oss-20b", "x"])).ensure_model()


def test_ensure_model_ignores_network_errors():
    GroqChat(model="ok", client=_client_with_models(error=ConnectionError("down"))).ensure_model()


def test_reasoning_effort_also_sent_for_qwen38(monkeypatch):
    monkeypatch.delenv("SJ_REASONING_EFFORT", raising=False)
    fake = FakeClient(reply("a"))
    GroqChat(model="qwen/qwen3.8-27b", client=fake).generate([{"role": "user", "text": "x"}])
    assert fake.sent[0]["reasoning_effort"] == "low"
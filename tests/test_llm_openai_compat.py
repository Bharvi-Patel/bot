"""OpenAICompatChat without the network (same fake-client style as test_llm_groq)."""
import re
from types import SimpleNamespace as NS

import pytest

from sjbot.llm import get_llm
from sjbot.llm_openai_compat import OpenAICompatChat
from tests.test_llm_groq import FakeClient, _client_with_models, reply


def history(turn):
    return [{"role": "user", "text": "hi"},
            {"role": "assistant", "text": "", "tool_calls": turn.tool_calls, "raw": turn.raw},
            {"role": "tool", "name": "list_brands", "result": {"count": 19}},
            {"role": "tool", "name": "get_store_info", "result": {"phone": "555"}}]


def test_mistral_ids_are_9_alphanumeric_and_consistent():
    fake = FakeClient(reply(calls=[("call_abc-123_long", "list_brands", "{}"), ("x", "get_store_info", "{}")]), reply("ok"))
    llm = OpenAICompatChat("mistral", client=fake)
    turn = llm.generate([{"role": "user", "text": "hi"}])
    assert llm.generate(history(turn)).text == "ok"
    sent = fake.sent[1]["messages"]
    call_ids = [tc["id"] for m in sent for tc in m.get("tool_calls") or []]
    tool_ids = [m["tool_call_id"] for m in sent if m["role"] == "tool"]
    assert call_ids == tool_ids and len(set(call_ids)) == 2
    assert all(re.fullmatch(r"[A-Za-z0-9]{9}", i) for i in call_ids)


def test_openrouter_keeps_original_ids_and_sends_no_reasoning_effort():
    fake = FakeClient(reply(calls=[("id1", "list_brands", "{}"), ("id2", "get_store_info", "{}")]), reply("ok"))
    llm = OpenAICompatChat("openrouter", model="openai/gpt-oss-20b:free", client=fake)
    turn = llm.generate([{"role": "user", "text": "hi"}])
    llm.generate(history(turn))
    assert [m["tool_call_id"] for m in fake.sent[1]["messages"] if m["role"] == "tool"] == ["id1", "id2"]
    assert "reasoning_effort" not in fake.sent[0]


def test_presets_default_models_and_override(monkeypatch):
    monkeypatch.delenv("SJ_LLM_MODEL", raising=False)
    assert OpenAICompatChat("mistral", client=FakeClient()).model == "mistral-small-latest"
    monkeypatch.setenv("SJ_LLM_MODEL", "mistral-large-latest")
    assert OpenAICompatChat("mistral", client=FakeClient()).model == "mistral-large-latest"


def test_missing_key_message(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
        OpenAICompatChat("mistral")


def test_unknown_preset():
    with pytest.raises(RuntimeError):
        OpenAICompatChat("nope", client=FakeClient())


def test_null_args_dropped_and_tokens_counted():
    r = reply(calls=[("a", "get_shipping_options", '{"zone_keyword": null}')])
    r.usage = NS(total_tokens=42)
    llm = OpenAICompatChat("openrouter", client=FakeClient(r))
    turn = llm.generate([{"role": "user", "text": "x"}])
    assert turn.tool_calls[0].args == {} and llm.tokens == 42


def test_ensure_model_lists_alternatives():
    with pytest.raises(SystemExit) as e:
        OpenAICompatChat("mistral", model="gone", client=_client_with_models(["a-model"])).ensure_model()
    assert "gone" in str(e.value) and "a-model" in str(e.value)


def test_ensure_model_ignores_gateways_without_a_model_list():
    OpenAICompatChat("openai", model="x", client=_client_with_models(error=ConnectionError("404"))).ensure_model()


def test_get_llm_dispatch(monkeypatch):
    monkeypatch.setenv("SJ_LLM_PROVIDER", "mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setattr(OpenAICompatChat, "ensure_model", lambda self: None)
    llm = get_llm()
    assert type(llm).__name__ == "OpenAICompatChat" and llm.preset == "mistral"
import json

import httpx
import pytest

from sjbot import llm as llm_module
from sjbot.guardrails import SYSTEM_PROMPT
from sjbot.llm_base import ToolCall
from sjbot.llm_ollama import OllamaChat
from sjbot.tool_schemas import TOOL_DECLARATIONS


def chat(handler, model="gpt-oss:20b", **env):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaChat(model=model, host="localhost:11434", client=client)


def reply(message, prompt=100, out=20):
    return httpx.Response(200, json={"message": message, "prompt_eval_count": prompt, "eval_count": out, "done": True})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("SJ_OLLAMA_NUM_CTX", "SJ_OLLAMA_NUM_PREDICT", "SJ_OLLAMA_TIMEOUT", "SJ_OLLAMA_KEEP_ALIVE", "SJ_OLLAMA_THINK", "SJ_REASONING_EFFORT", "OLLAMA_HOST", "SJ_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_request_has_context_window_tools_system_prompt_and_no_streaming():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return reply({"role": "assistant", "content": " Hello! "})

    turn = chat(handler).generate([{"role": "user", "text": "hi"}])
    body = seen["body"]
    assert seen["url"] == "http://localhost:11434/api/chat" and turn.text == "Hello!" and turn.tool_calls == []
    assert body["model"] == "gpt-oss:20b" and body["stream"] is False and body["options"] == {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1024}
    assert body["keep_alive"] == "30m" and body["think"] == "low"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT} and body["messages"][1] == {"role": "user", "content": "hi"}
    assert [t["function"]["name"] for t in body["tools"]] == [d["name"] for d in TOOL_DECLARATIONS]


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("SJ_OLLAMA_NUM_CTX", "16384")
    monkeypatch.setenv("SJ_OLLAMA_KEEP_ALIVE", "5m")
    monkeypatch.setenv("SJ_OLLAMA_THINK", "off")
    seen = {}
    chat(lambda r: (seen.update(b=json.loads(r.content)), reply({"content": "ok"}))[1]).generate([{"role": "user", "text": "x"}])
    assert seen["b"]["options"]["num_ctx"] == 16384 and seen["b"]["keep_alive"] == "5m" and seen["b"]["think"] is False


def test_thinking_defaults_low_for_gpt_oss_and_off_for_everything_else():
    seen = []
    handler = lambda r: (seen.append(json.loads(r.content)), reply({"content": "ok"}))[1]
    chat(handler, model="qwen3:8b").generate([{"role": "user", "text": "x"}])
    chat(handler, model="gpt-oss:120b").generate([{"role": "user", "text": "x"}])
    assert seen[0]["think"] is False and seen[1]["think"] == "low"


def test_the_reply_length_cap_can_be_changed_or_removed(monkeypatch):
    seen = []
    handler = lambda r: (seen.append(json.loads(r.content)["options"]), reply({"content": "ok"}))[1]
    monkeypatch.setenv("SJ_OLLAMA_NUM_PREDICT", "256")
    chat(handler).generate([{"role": "user", "text": "x"}])
    monkeypatch.setenv("SJ_OLLAMA_NUM_PREDICT", "0")
    chat(handler).generate([{"role": "user", "text": "x"}])
    assert seen[0]["num_predict"] == 256 and "num_predict" not in seen[1]


def test_a_timeout_becomes_a_message_that_says_what_to_check():
    def slow(request):
        raise httpx.ReadTimeout("timed out")
    with pytest.raises(RuntimeError, match="ollama ps"):
        chat(slow, model="qwen3:8b").generate([{"role": "user", "text": "x"}])


def test_tool_call_roundtrip_in_the_conversation():
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return reply({"content": "Done."})

    msgs = [{"role": "user", "text": "brands?"},
            {"role": "assistant", "text": "", "tool_calls": [ToolCall("list_brands", {}), ToolCall("search_products", {"keyword": "sofa", "max_price": 250})]},
            {"role": "tool", "name": "list_brands", "result": {"brands": ["Acme"]}},
            {"role": "tool", "name": "search_products", "result": {"count": 0}}]
    chat(handler).generate(msgs)
    m = seen["messages"]
    assert m[2]["tool_calls"] == [{"function": {"name": "list_brands", "arguments": {}}},
                                  {"function": {"name": "search_products", "arguments": {"keyword": "sofa", "max_price": 250}}}]
    assert m[3] == {"role": "tool", "tool_name": "list_brands", "content": '{"brands": ["Acme"]}'} and m[4]["tool_name"] == "search_products"


def test_tool_calls_are_parsed_with_null_arguments_dropped_and_string_arguments_decoded():
    message = {"content": "", "tool_calls": [
        {"function": {"name": "search_products", "arguments": {"keyword": "sofa", "color": None, "max_price": 250}}},
        {"function": {"name": "list_brands", "arguments": "{}"}},
        {"function": {"name": "get_store_info", "arguments": "not json"}},
        {"function": {"arguments": {"x": 1}}}]}
    turn = chat(lambda r: reply(message)).generate([{"role": "user", "text": "x"}])
    assert [(c.name, c.args) for c in turn.tool_calls] == [("search_products", {"keyword": "sofa", "max_price": 250}),
                                                           ("list_brands", {}), ("get_store_info", {})]


def test_tokens_are_counted_from_prompt_and_reply():
    llm = chat(lambda r: reply({"content": "a"}, prompt=2500, out=40))
    llm.generate([{"role": "user", "text": "x"}])
    llm.generate([{"role": "user", "text": "x"}])
    assert llm.tokens == 5080


def test_a_broken_tool_call_is_retried_with_a_hint():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content)["messages"])
        if len(bodies) == 1:
            return httpx.Response(500, json={"error": "error parsing tool call: invalid character"})
        return reply({"content": "fixed"})

    turn = chat(handler).generate([{"role": "user", "text": "x"}])
    assert turn.text == "fixed" and len(bodies) == 2 and "Never pass null" in bodies[1][-1]["content"]


def test_a_model_without_thinking_support_is_retried_without_it():
    thinks = []

    def handler(request):
        body = json.loads(request.content)
        thinks.append(body.get("think", "absent"))
        return httpx.Response(400, json={"error": "\"gpt-oss\" does not support thinking"}) if "think" in body else reply({"content": "ok"})

    llm = chat(handler)
    assert llm.generate([{"role": "user", "text": "x"}]).text == "ok" and thinks == ["low", "absent"]
    llm.generate([{"role": "user", "text": "again"}])
    assert thinks == ["low", "absent", "absent"]                  # it remembers: no failed first attempt on later calls


def test_other_errors_propagate():
    with pytest.raises(httpx.HTTPStatusError, match="500"):
        chat(lambda r: httpx.Response(500, json={"error": "model runner crashed"})).generate([{"role": "user", "text": "x"}])


# ---------------------------------------------------------------- startup check
def tags(*names):
    return lambda r: httpx.Response(200, json={"models": [{"name": n} for n in names]})


def test_ensure_model_accepts_a_pulled_model_with_or_without_latest():
    chat(tags("gpt-oss:20b", "qwen3:8b")).ensure_model()
    chat(tags("llama3.1:latest"), model="llama3.1").ensure_model()


def test_ensure_model_says_how_to_pull_and_lists_what_exists():
    with pytest.raises(SystemExit) as e:
        chat(tags("qwen3:8b", "llama3.1:latest")).ensure_model()
    msg = str(e.value)
    assert "ollama pull gpt-oss:20b" in msg and "qwen3:8b" in msg and "llama3.1:latest" in msg


def test_ensure_model_explains_when_ollama_is_not_running():
    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(SystemExit, match="Ollama is not running at http://localhost:11434"):
        chat(down).ensure_model()


def test_ensure_model_ignores_odd_responses():
    chat(lambda r: httpx.Response(500, text="boom")).ensure_model()


# ---------------------------------------------------------------- provider switch
def test_provider_switch_builds_the_ollama_adapter(monkeypatch):
    monkeypatch.setenv("SJ_LLM_PROVIDER", "ollama")
    monkeypatch.setattr("sjbot.llm_ollama.OllamaChat.ensure_model", lambda self: None)
    assert type(llm_module.get_llm()).__name__ == "OllamaChat"
    monkeypatch.setenv("SJ_LLM_PROVIDER", "nope")
    with pytest.raises(RuntimeError, match="ollama"):
        llm_module.get_llm()
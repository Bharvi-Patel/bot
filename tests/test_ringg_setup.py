"""dev_tools/ringg_setup.py: builds a Ringg agent from a spec. No test here reaches the network."""
import json
from pathlib import Path

import pytest

from bot.ringg import VOICE_TOOLS
from dev_tools import ringg_setup as rs

SPEC_PATH = Path(__file__).resolve().parent.parent / "dev_tools" / "ringg_agents" / "b3-dev.json"
KEY = "ringg-key-DO-NOT-PRINT"
SECRET = "webhook-secret-DO-NOT-PRINT"
VOICE = "3b5c7d2a-8e9f-4a1b-9c6d-2e5f8a7b3c4d"
ENV = {"RINGG_API_KEY": KEY, "BOT_PUBLIC_URL": "https://bot.example.test", "RINGG_WEBHOOK_SECRET": SECRET}


def spec(**changes):
    s = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    s["voice_id"] = VOICE
    s.update(changes)
    return s


def write_spec(tmp_path, **changes):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec(**changes)), encoding="utf-8")
    return str(path)


class Fake:
    """A stand-in for Ringg: records every request and answers from a table."""
    def __init__(self, agents=(), create=(200, None), fail_step=None):
        self.calls, self.agents, self.create, self.fail_step = [], list(agents), create, fail_step

    def __call__(self, method, url, headers, body):
        data = json.loads(body) if body else None
        self.calls.append((method, url.replace(rs.BASE_URL, ""), headers, data))
        if method == "GET" and url.endswith("/agent/all"):
            return 200, json.dumps({"data": {"agents": [{"agent_display_name": a} for a in self.agents]}}).encode()
        if method == "POST":
            status, payload = self.create
            return status, json.dumps(payload if payload is not None else {"success": True, "data": {"id": "new-agent-id-1234"}}).encode()
        if method == "PATCH":
            if self.fail_step and data["operation"] == self.fail_step:
                return 500, json.dumps({"message": "Failed to edit agent"}).encode()
            return 200, json.dumps({"message": "Agent updated successfully!"}).encode()
        return 200, json.dumps({"voices": [{"id": VOICE, "name": "Sarah", "gender": "female", "languages": ["en-US"]}]}).encode()


def run(argv, env=ENV, send=None):
    lines = []
    code = rs.main(argv, env=env, out=lines.append, send=send or Fake())
    return code, "\n".join(lines)


# ---------- the spec ----------

def test_the_shipped_spec_is_valid_and_needs_a_voice_chosen():
    s = rs.load_spec(SPEC_PATH)
    assert s["agent_name"] == "B3 DEV" and {"store_uuid", "store_name"} <= set(s["custom_variables"])
    s = {**s, "voice_id": ""}                      # whatever voice has been put in the file, an empty one must be refused
    assert rs.build_requests(s, ENV)["problems"] == ["voice_id is empty: run `voices --language en-US` and put a voice id in the spec"]


@pytest.mark.parametrize("change", [
    {"surprise": 1}, {"vertical": "spaceships"}, {"agent_type": "sideways"}, {"primary_language": "xx-XX"}, {"voice_id": "nope"},
    {"agent_name": ""}, {"agent_name": "x" * 101}, {"intro_message": "x" * 501},
    {"custom_variables": ["store_name"]}, {"custom_variables": ["store_uuid", "Store Name"]},
    {"whitelisted_domains": ["https://shop.example.test"]},                      # must keep https://www.ringg.ai
    {"whitelisted_domains": ["https://www.ringg.ai", "http://plain.example.test"]},
    {"event_subscriptions": [{"event_type": ["not_an_event"], "callback_url": "https://x.test"}]},
    {"agent_type": "outbound"},                                                   # outbound must keep callee_name and mobile_number
])
def test_a_bad_spec_is_refused(change):
    with pytest.raises(rs.SetupError):
        rs.check_spec(spec(**change))


# ---------- what is built ----------

def test_the_create_request_follows_the_documented_limits_and_the_prompt_is_set_in_a_second_step():
    built = rs.build_requests(spec(), ENV)
    c = built["create"]
    assert not built["problems"]
    assert {"agent_name", "introduction_and_objective", "response_guidelines", "task", "primary_language", "voice_id",
            "intro_message"} <= set(c)
    assert len(c["agent_name"]) <= 100 and len(c["intro_message"]) <= 500
    assert len(c["introduction_and_objective"]) <= 2000 and len(c["response_guidelines"]) <= 3000 and len(c["task"]) <= 1500
    assert c["agent_type"] == "inbound" and c["custom_variables"] == ["store_uuid", "store_name", "conversation_id"]
    ops = [body["operation"] for _, body in built["steps"]]
    assert ops == ["edit_prompt", "edit_agent_whitelisted_domains", "edit_event_subscriptions"]
    prompt = built["steps"][0][1]["agent_prompt"]
    assert prompt == built["prompt"] and prompt.count("{{store_name}}") == 1 and "@store_name" not in prompt
    assert "get_order_status" not in prompt
    assert len(prompt) > 3000                                                    # why the prompt cannot go into create


def test_environment_values_fill_the_webhook_and_missing_ones_are_named_without_values():
    built = rs.build_requests(spec(), ENV)
    sub = built["steps"][2][1]["event_subscriptions"][0]
    assert sub["callback_url"] == "https://bot.example.test/webhooks/ringg" and sub["headers"] == {"X-Webhook-Secret": SECRET}
    bad = rs.build_requests(spec(), {})
    assert bad["problems"] == ["not set in the environment: BOT_PUBLIC_URL, RINGG_WEBHOOK_SECRET"]
    plain = rs.build_requests(spec(), {**ENV, "BOT_PUBLIC_URL": "http://localhost:8100"})
    assert any("https://" in p for p in plain["problems"])


def test_the_plan_never_shows_a_secret_or_the_whole_prompt():
    text = "\n".join(rs.plan_lines(rs.build_requests(spec(), ENV)))
    assert SECRET not in text and KEY not in text and "X-Webhook-Secret" in text and "***" in text
    assert "characters" in text and "Never reveal costs, margins" not in text          # only a summary of the prompt, not all of it


def test_mask_hides_header_values_and_secret_named_fields_only():
    m = rs.mask({"headers": {"Authorization": "Bearer abc", "X-Other": "v"}, "api_key": "k", "agent_name": "B3 DEV", "n": [{"token": "t"}]})
    assert m == {"headers": {"Authorization": "***", "X-Other": "***"}, "api_key": "***", "agent_name": "B3 DEV", "n": [{"token": "***"}]}


def test_domains_come_from_enabled_store_files_only(tmp_path):
    (tmp_path / "s1.json").write_text(json.dumps({"store_name": "A", "allowed_domains": ["Shop.Example.test", "shop.example.test"]}))
    (tmp_path / "s2.json").write_text(json.dumps({"store_name": "B", "enabled": False, "allowed_domains": ["off.example.test"]}))
    (tmp_path / "s3.json").write_text("not json")
    (tmp_path / "s4.json").write_text(json.dumps({"store_name": "D", "allowed_domains": ["bad host", "ok.example.test"]}))
    assert rs.domains_from_stores(tmp_path) == ["https://shop.example.test", "https://ok.example.test"]
    domains = rs.build_requests(spec(), ENV, str(tmp_path))["steps"][1][1]["whitelisted_domains"]
    assert domains[0] == "https://www.ringg.ai" and "https://shop.example.test" in domains and "https://off.example.test" not in domains


# ---------- create ----------

def test_create_is_a_dry_run_unless_yes_is_given(tmp_path):
    fake = Fake()
    code, out = run(["create", write_spec(tmp_path)], send=fake)
    assert code == 0 and "Dry run: nothing was sent" in out and fake.calls == []


def test_create_with_yes_makes_one_agent_then_sets_the_rest_and_never_prints_the_key(tmp_path):
    fake = Fake()
    code, out = run(["create", write_spec(tmp_path), "--yes"], send=fake)
    assert code == 0
    assert [(m, p) for m, p, _, _ in fake.calls] == [("GET", "/agent/all"), ("POST", "/public/agent/"), ("PATCH", "/agent/v1"),
                                                      ("PATCH", "/agent/v1"), ("PATCH", "/agent/v1")]
    assert all(h["X-API-KEY"] == KEY for _, _, h, _ in fake.calls)
    assert [b["operation"] for m, _, _, b in fake.calls if m == "PATCH"] == ["edit_prompt", "edit_agent_whitelisted_domains",
                                                                              "edit_event_subscriptions"]
    assert all(b["agent_id"] == "new-agent-id-1234" for m, _, _, b in fake.calls if m == "PATCH")
    assert KEY not in out and SECRET not in out
    assert '"new-agent-id-1234": "*"' in out and "tools" in out


def test_create_refuses_a_duplicate_name_and_creates_nothing(tmp_path):
    fake = Fake(agents=["B3 DEV"])
    code, out = run(["create", write_spec(tmp_path), "--yes"], send=fake)
    assert code == 1 and "already exists" in out and all(m == "GET" for m, _, _, _ in fake.calls)


def test_create_stops_on_problems_before_any_request(tmp_path):
    fake = Fake()
    code, out = run(["create", write_spec(tmp_path, voice_id=""), "--yes"], send=fake)
    assert code == 1 and "voice_id is empty" in out and fake.calls == []


def test_a_failed_step_names_the_agent_and_says_not_to_create_it_again(tmp_path):
    code, out = run(["create", write_spec(tmp_path), "--yes"], send=Fake(fail_step="edit_agent_whitelisted_domains"))
    assert code == 1 and "new-agent-id-1234" in out and "Do not create it again" in out and "Failed to edit agent" in out
    assert KEY not in out


def test_a_failed_create_reports_the_ringg_code(tmp_path):
    code, out = run(["create", write_spec(tmp_path), "--yes"],
                    send=Fake(create=(400, {"message": "Voice does not support language 'en-US'", "code": "VOICE_LANGUAGE_MISMATCH"})))
    assert code == 1 and "VOICE_LANGUAGE_MISMATCH" in out


# ---------- the key and the network ----------

@pytest.mark.parametrize("argv", [["voices"], ["show", "abcdef123456"]])
def test_commands_that_need_the_key_refuse_without_it_and_send_nothing(argv):
    fake = Fake()
    code, out = run(argv, env={}, send=fake)
    assert code == 1 and "RINGG_API_KEY is not set" in out and fake.calls == []


def test_create_with_yes_needs_the_key_too(tmp_path):
    fake = Fake()
    code, out = run(["create", write_spec(tmp_path), "--yes"], env={k: v for k, v in ENV.items() if k != "RINGG_API_KEY"}, send=fake)
    assert code == 1 and "RINGG_API_KEY is not set" in out and fake.calls == []


def test_requests_go_only_to_the_ringg_host():
    with pytest.raises(rs.SetupError):
        rs._urllib_send("GET", "https://evil.example.test/agent/all", {}, None)


def test_a_network_error_never_echoes_the_key():
    def boom(*_):
        raise OSError(f"connection failed for {KEY}")
    code, out = run(["voices"], send=boom)
    assert code == 1 and KEY not in out and "could not reach Ringg" in out


def test_voices_lists_ids_names_and_languages():
    fake = Fake()
    code, out = run(["voices", "--language", "en-US"], send=fake)
    assert code == 0 and VOICE in out and "Sarah" in out and fake.calls[0][1] == "/agent/voices?language=en-US"


# ---------- show ----------

def test_show_prints_names_and_sizes_but_no_secret_values_or_tool_settings():
    data = {"agents": {"agent_type": "inbound", "active_agent_version_id": "v1", "version_details": {"v1": {
        "language": "en-US", "voice": {"name": "Sarah"},
        "agent_config": {"custom_variables": ["store_uuid", "store_name"], "intro_message": "<p>Hi {{store_name}}</p>",
                         "agent_prompt": {"prompt_sections": [{"section_title": "Rules", "section_content": "You are for {{store_name}}."}]}},
        "whitelisted_domains": ["https://www.ringg.ai"],
        "event_subscriptions": [{"event_type": ["call_completed"], "callback_url": "https://bot.example.test/webhooks/ringg",
                                 "headers": {"X-Webhook-Secret": "SECRETVALUE"}}],
        "on_call_tools": [{"tool_name": "get_store_info", "tool_type": "WORKSPACE_TOOL", "config": {"url": "https://hidden.example.test"}}],
    }}}}
    text = "\n".join(rs.summarize_agent(data))
    assert "SECRETVALUE" not in text and "hidden.example.test" not in text
    assert "X-Webhook-Secret" in text and "get_store_info" in text and "{{store_name}} x1" in text


def test_show_accepts_the_prompt_as_a_plain_string():
    data = {"agents": {"agent_type": "inbound", "active_agent_version_id": "v1", "version_details": {"v1": {
        "language": "en-US", "agent_config": {"agent_prompt": "You are for {{store_name}}.", "intro_message": "Hi"}}}}}
    text = "\n".join(rs.summarize_agent(data))
    assert "prompt: 27 characters, {{store_name}} x1" in text


# ---------- the tool checklist ----------

def test_the_tool_checklist_covers_the_voice_tools_and_never_makes_the_store_dynamic():
    checklist = rs.tool_checklist("https://bot.example.test/")
    assert [t["name"] for t in checklist] == list(VOICE_TOOLS) and "get_order_status" not in [t["name"] for t in checklist]
    for t in checklist:
        assert t["url"] == f"https://bot.example.test/ringg/tools/{t['name']}" and t["method"] == "POST"
        kinds = {h["name"]: h["kind"] for h in t["headers"]}
        assert kinds["Store-UUID"] == "variable" and kinds["X-Ringg-Tools-Key"] == "static" and "dynamic" not in kinds.values()
        assert "store_uuid" not in [p["name"] for p in t["parameters"]]
    assert any("search_products" in line for line in rs.tool_lines(checklist))
    assert "RINGG_TOOLS_KEY" in json.dumps(checklist) and ENV["RINGG_API_KEY"] not in json.dumps(checklist)


# ---------- it stays out of the service ----------

def test_the_bot_service_never_imports_the_setup_script():
    for path in (Path(__file__).resolve().parent.parent / "bot").rglob("*.py"):
        assert "ringg_setup" not in path.read_text(encoding="utf-8"), path

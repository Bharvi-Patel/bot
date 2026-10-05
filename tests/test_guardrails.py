from sjbot import guardrails as g


def test_unknown_tools_are_refused():
    assert g.run_tool("run_sql", {"sql": "select 1"}) == {"error": "unknown_tool"}
    assert g.run_tool("get_customer", {"email": "a@b.co"}) == {"error": "unknown_tool"}


def test_order_tool_gets_the_context_and_other_tools_do_not(monkeypatch):
    seen = {}
    monkeypatch.setitem(g.TOOLS, "get_order_status", lambda a, ctx=None: seen.update(ctx=ctx) or {"found": False})
    monkeypatch.setitem(g.TOOLS, "list_brands", lambda a: {"brands": []})       # would raise TypeError if given ctx=
    g.run_tool("get_order_status", {"order_number": "1"}, ctx="the-ctx")
    assert seen["ctx"] == "the-ctx"
    assert g.run_tool("list_brands", {}, ctx="the-ctx") == {"brands": []}


def test_order_tool_without_context_does_not_look_anything_up():
    out = g.run_tool("get_order_status", {"order_number": "1", "email": "a@b.co"})
    assert out["error"] == "verification_unavailable"


def test_non_dict_args_rejected():
    assert "error" in g.run_tool("list_brands", "oops")


def test_tool_crash_is_hidden(monkeypatch):
    def boom(args):
        raise RuntimeError("Access denied for user chatbot_ro")
    monkeypatch.setitem(g.TOOLS, "list_brands", boom)
    out = g.run_tool("list_brands", {})
    assert out["error"] == "tool_failed" and "chatbot_ro" not in str(out)


def test_tool_input_errors_come_back_as_data():
    assert "error" in g.run_tool("check_inventory", {"bogus": 1})


def test_price_must_come_from_tools():
    results = [{"price": 899.0}]
    assert g.check_reply("The sofa is $899.", results) == (True, None)
    assert g.check_reply("The sofa is $799.", results) == (False, "unverified_price")


def test_user_budget_can_be_echoed():
    assert g.check_reply("Here are sofas under $1,000.", [], "show me sofas under $1,000") == (True, None)


def test_internal_leak_blocked():
    assert g.check_reply("My base_price is 400", [])[1] == "internal_leak"
    assert g.check_reply("I read it from vw_chat_products", [])[1] == "internal_leak"


def test_only_store_email_allowed():
    results = [{"email": "info@store.com"}]
    assert g.check_reply("Email info@store.com", results) == (True, None)
    assert g.check_reply("Email bob@gmail.com", results) == (False, "unknown_email")


def test_customer_may_see_their_own_typed_email_but_not_an_invented_one():
    msg = "where is order 1001, my email is jo@example.com"
    assert g.check_reply("No order matches 1001 and jo@example.com.", [{"found": False}], msg) == (True, None)
    assert g.check_reply("Write to sam@example.com", [{"found": False}], msg) == (False, "unknown_email")


def test_prompt_covers_orders():
    assert "get_order_status" in g.SYSTEM_PROMPT and "cannot look up orders" not in g.SYSTEM_PROMPT

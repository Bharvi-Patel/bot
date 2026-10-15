"""A markdown link whose target is a tool name is cut out; the rest of the answer is kept. Other leaks still block."""
import pytest

from sjbot import guardrails as g
from sjbot import router
from test_router import FakeLLM, call, say


@pytest.fixture(autouse=True)
def no_review_log(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "REVIEW_LOG", tmp_path / "review.jsonl")


# from eval run 2026-10-15, memory-vs-hybrid run 3 (blocked as internal_leak)
LEAKY = ("Hybrids are best for balanced support.\n\nFor more details or to discuss which might suit your needs best, "
         "you can visit our showroom or call us. [Get store contact details](get_store_info).")


def test_the_link_to_a_tool_name_is_cut_and_the_answer_kept():
    assert g.check_reply(LEAKY, [], "difference between memory foam and hybrid?") == (False, "internal_leak")
    fixed = g.strip_tool_name_links(LEAKY)
    assert fixed.endswith("visit our showroom or call us.") and "get_store_info" not in fixed and ".." not in fixed
    assert g.check_reply(fixed, [], "difference between memory foam and hybrid?") == (True, None)


def test_normal_links_and_bare_tool_names_are_not_touched():
    assert g.strip_tool_name_links("See our [Financing page](https://southjacksonfurniture.com/financing).") is None
    assert g.strip_tool_name_links("I used get_store_info to look that up.") is None          # bare name: no repair, still blocked
    assert g.check_reply("I used get_store_info to look that up.", [], "hi")[1] == "internal_leak"


def test_router_sends_the_cleaned_reply_not_the_fallback(monkeypatch):
    monkeypatch.setitem(g.TOOLS, "get_store_info", lambda a: {"hours": "9-7"})
    out = router.answer("what are your hours?", FakeLLM(call("get_store_info"), say("We open 9-7. [Store details](get_store_info).")))
    assert out["blocked"] is None and out["reply"] == "We open 9-7."
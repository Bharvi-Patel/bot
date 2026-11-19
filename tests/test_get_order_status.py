"""get_order_status: the logged-in customer's own orders, read through the store API with the customer's own token."""
import json
import logging

import pytest

from bot.api_client import ApiError
from bot.context import ChatContext
from bot.tools import get_order_status as gos
from bot.tools.get_order_status import (LOGIN_EXPIRED, LOGIN_REQUIRED, NO_MATCH, NO_ORDERS, NOT_AVAILABLE, get_order_status)

TOKEN = "eyJhbGciOiJIUzI1NiJ9.SECRET-PAYLOAD.SECRET-SIGNATURE"
CTX = ChatContext(store_uuid="store-a", client_id="203.0.113.7", customer_uuid="cust-9", customer_token=TOKEN)

ORDERS = [
    {"order_number": "102500001", "order_status": "Processing", "created_at": "2026-10-02T14:11:09.000000Z", "grand_total": 879.98,
     "items": [{"name": "Blanche Side Chair Brown", "sku": "2261BRN-S"}, {"name": "Blanche Bench", "sku": None}]},
    {"order_number": "102500002", "order_status": "Completed", "created_at": "2026-10-20T09:30:00.000000Z", "grand_total": 159.99, "items": []},
    {"order_number": "102400007", "order_status": None, "created_at": None, "grand_total": 0.0, "items": []},
]


class FakeClient:
    def __init__(self, orders=ORDERS, error=None):
        self.orders, self.error, self.calls = orders, error, []

    def customer_orders(self, token, limit=25):
        self.calls.append((token, limit))
        if self.error:
            raise self.error
        return self.orders


def run(args, ctx=CTX, client=None):
    return get_order_status(args, ctx=ctx, client=client or FakeClient())


# ---- who may ask ----
@pytest.mark.parametrize("ctx", [None, ChatContext(), ChatContext(store_uuid="s", customer_uuid="c-1"),
                                 ChatContext(store_uuid="s", client_id="x", customer_token=None)])
def test_a_visitor_gets_a_log_in_answer_and_no_request_is_made(ctx):
    client = FakeClient()
    assert get_order_status({"order_number": "102500001"}, ctx=ctx, client=client) == LOGIN_REQUIRED
    assert client.calls == []


def test_a_customer_id_without_a_token_is_not_enough():
    assert run({"order_number": "102500001"}, ChatContext(store_uuid="s", customer_uuid="cust-9")) == LOGIN_REQUIRED


def test_the_log_in_answer_tells_the_model_not_to_collect_personal_details():
    assert "Do not ask for an order number, email or any personal details" in LOGIN_REQUIRED["note"]


def test_there_is_no_guest_lookup_by_email_any_more():
    out = run({"order_number": "102500001", "email": "someone@example.com"})
    assert out == {"error": "unknown parameter(s): email"}
    assert gos.ALLOWED_KEYS == {"order_number"}


# ---- what comes back ----
def test_one_order_by_number_has_only_the_fields_the_bot_may_state():
    client = FakeClient()
    out = run({"order_number": "102500001"}, client=client)
    assert set(out) == {"found", "order_number", "order_status", "placed_on", "grand_total", "items", "note"}
    assert out["found"] is True and out["order_number"] == "102500001" and out["order_status"] == "Processing"
    assert out["placed_on"] == "2026-10-02" and out["grand_total"] == 879.98
    assert out["items"] == [{"name": "Blanche Side Chair Brown", "sku": "2261BRN-S"}, {"name": "Blanche Bench", "sku": None}]
    assert out["note"] == gos.STATUS_NOTE
    assert client.calls == [(TOKEN, gos.MAX_ORDERS)]


@pytest.mark.parametrize("typed", ["#102500001", " 102500001 ", "102500001"])
def test_the_number_is_matched_after_cleaning_it_up(typed):
    assert run({"order_number": typed})["order_number"] == "102500001"


def test_without_a_number_the_recent_orders_are_listed():
    out = run({})
    assert out["found"] is True and [o["order_number"] for o in out["orders"]] == ["102500001", "102500002", "102400007"]
    assert out["orders"][2]["order_status"] == "not on file" and out["orders"][2]["placed_on"] is None
    assert "note" in out


def test_at_most_five_recent_orders_are_listed():
    many = [{"order_number": f"10{n}", "order_status": "x", "created_at": None, "grand_total": 1.0, "items": []} for n in range(9)]
    assert len(run({}, client=FakeClient(many))["orders"]) == gos.LIST_LIMIT


def test_an_account_with_no_orders_says_so():
    assert run({}, client=FakeClient([])) == NO_ORDERS


# ---- misses look the same, whatever the reason ----
def test_another_customers_or_an_unknown_order_is_just_not_found():
    assert run({"order_number": "999999999"}) == NO_MATCH


@pytest.mark.parametrize("bad", ["x" * 31, "1 2", "1;DROP", "../1", "", "#"])
def test_a_badly_formed_number_is_refused_without_a_request(bad):
    client = FakeClient()
    out = run({"order_number": bad}, client=client)
    assert out == NO_MATCH or "error" in out
    assert client.calls == []


def test_a_number_that_is_not_text_is_an_error():
    assert "error" in run({"order_number": 123})


# ---- failures ----
@pytest.mark.parametrize("status", [401, 403])
def test_an_expired_or_refused_login_asks_to_log_in_again(status):
    assert run({"order_number": "102500001"}, client=FakeClient(error=ApiError("Unauthenticated", status=status))) == LOGIN_EXPIRED


@pytest.mark.parametrize("error", [ApiError("boom", status=500), ApiError("could not reach", retryable=True), ApiError("odd shape")])
def test_any_other_api_failure_is_unavailable(error):
    assert run({"order_number": "102500001"}, client=FakeClient(error=error)) == NOT_AVAILABLE


# ---- the token never leaks ----
def test_the_token_is_never_in_a_result_or_a_log(caplog):
    caplog.set_level(logging.DEBUG)
    results = [run({"order_number": "102500001"}), run({}), run({"order_number": "1"}),
               run({"order_number": "102500001"}, client=FakeClient(error=ApiError(f"bad {TOKEN}", status=500))),
               run({}, client=FakeClient(error=ApiError("x", status=401)))]
    blob = json.dumps(results) + caplog.text
    assert "SECRET" not in blob and TOKEN not in blob


def test_the_token_is_not_in_the_contexts_printout_and_does_not_affect_equality():
    ctx = ChatContext(store_uuid="s", customer_token=TOKEN)
    assert TOKEN not in repr(ctx) and "SECRET" not in str(ctx)
    assert ctx == ChatContext(store_uuid="s", customer_token="another")


def test_the_tool_never_touches_a_database():
    import pathlib
    text = pathlib.Path(gos.__file__).read_text(encoding="utf8").lower()
    assert "bot.db" not in text and "vw_chat" not in text and "run_query" not in text and "mysql" not in text

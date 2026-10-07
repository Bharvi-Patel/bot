import hashlib
import re

import pytest

from sjbot.context import ChatContext
from sjbot.tools.get_order_status import (AttemptLimiter, NO_MATCH, NOT_AVAILABLE, RATE_LIMITED, get_order_status)

EMAIL = "order2@example.test"
HASH = hashlib.sha256(EMAIL.encode()).hexdigest()
ORDER = {"order_uuid": "u-1", "order_number": "102500001", "order_status": "Paid", "grand_total": 1952.5,
         "created_at": "2025-10-14T13:43:37"}
ITEMS = [{"product_name": "Loreo Sofa", "product_sku": "LOR-1", "product_qty": 2.0}]
GUEST = ChatContext(client_id="1.2.3.4")


class FakeDB:
    """Answers like MySQL would: the order only comes back when the number and the email hash (or customer) match."""
    def __init__(self, customer_uuid="cust-1"):
        self.calls, self.customer_uuid = [], customer_uuid

    def __call__(self, sql, params=()):
        self.calls.append((sql, list(params)))
        assert re.findall(r"FROM\s+(\w+)", sql) and all(t.startswith("vw_chat_") for t in re.findall(r"FROM\s+(\w+)", sql))
        if "vw_chat_order_items" in sql:
            return [dict(r) for r in ITEMS]
        number, who = params[0], params[1]
        if number == "102500001" and who in (HASH, self.customer_uuid):
            return [dict(ORDER)]
        return []


def lookup(args, ctx=GUEST, db=None, limiter=None):
    return get_order_status(args, ctx=ctx, run_query=db or FakeDB(), limiter=limiter or AttemptLimiter())


# ---------- the happy paths ----------
def test_guest_with_right_order_and_email_gets_status_and_items():
    out = lookup({"order_number": "102500001", "email": EMAIL})
    assert out["found"] is True and out["order_status"] == "Paid" and out["placed_on"] == "2025-10-14"
    assert out["grand_total"] == 1952.5
    assert out["items"] == [{"name": "Loreo Sofa", "sku": "LOR-1", "quantity": 2}]


def test_email_is_normalised_and_hash_is_what_reaches_the_database():
    db = FakeDB()
    out = lookup({"order_number": " #102500001 ", "email": "  ORDER2@Example.TEST "}, db=db)
    assert out["found"] is True
    first_sql, first_params = db.calls[0]
    assert first_params == ["102500001", HASH] and "example.test" not in str(db.calls)


def test_logged_in_customer_matches_on_session_uuid_without_email():
    db = FakeDB()
    out = lookup({"order_number": "102500001"}, ctx=ChatContext(client_id="x", customer_uuid="cust-1"), db=db)
    assert out["found"] is True and "customer_uuid = %s" in db.calls[0][0] and db.calls[0][1] == ["102500001", "cust-1"]


def test_logged_in_customer_cannot_see_someone_elses_order_and_is_not_rate_limited():
    ctx = ChatContext(client_id="x", customer_uuid="someone-else")
    for n in range(20):
        assert lookup({"order_number": f"10250{n:04d}"}, ctx=ctx) == NO_MATCH


def test_status_missing_is_reported_as_not_on_file():
    db = lambda sql, params=(): [dict(ORDER, order_status=None)] if "vw_chat_orders" in sql else []
    assert lookup({"order_number": "102500001", "email": EMAIL}, db=db)["order_status"] == "not on file"


# ---------- never leaks whether an order exists ----------
def test_wrong_email_unknown_order_and_other_customers_order_all_look_identical():
    wrong_email = lookup({"order_number": "102500001", "email": "someone@else.com"})
    no_such_order = lookup({"order_number": "999999999", "email": EMAIL})
    odd_format = lookup({"order_number": "1; DROP TABLE orders", "email": EMAIL})
    assert wrong_email == no_such_order == odd_format == NO_MATCH


def test_only_the_agreed_fields_are_returned():
    out = lookup({"order_number": "102500001", "email": EMAIL})
    assert set(out) == {"found", "order_number", "order_status", "placed_on", "grand_total", "items", "note"}
    assert all(set(i) == {"name", "sku", "quantity"} for i in out["items"])


def test_payment_status_is_never_selected():
    db = FakeDB()
    lookup({"order_number": "102500001", "email": EMAIL}, db=db)
    assert all("payment_status" not in sql for sql, _ in db.calls)


# ---------- the model cannot widen access ----------
@pytest.mark.parametrize("extra", ["customer_uuid", "client_id", "order_uuid", "sql"])
def test_model_cannot_pass_identity_or_other_arguments(extra):
    db = FakeDB()
    out = lookup({"order_number": "102500001", "email": EMAIL, extra: "x"}, db=db)
    assert "error" in out and extra in out["error"] and db.calls == []


def test_input_problems_come_back_as_data_without_touching_the_database():
    db = FakeDB()
    assert lookup({}, db=db)["error"].startswith("order_number is required")
    assert lookup({"order_number": "  "}, db=db)["error"].startswith("order_number is required")
    assert lookup({"order_number": 1001}, db=db)["error"].startswith("order_number is required")
    assert lookup({"order_number": "102500001"}, db=db)["error"] == "email_required"
    assert lookup({"order_number": "102500001", "email": "not-an-email"}, db=db)["error"] == "invalid_email"
    assert lookup({"order_number": "102500001", "email": "a@b.co" + "x" * 300}, db=db)["error"] == "invalid_email"
    assert db.calls == []


def test_no_client_id_fails_closed_for_guests():
    for ctx in (None, ChatContext()):
        db = FakeDB()
        assert lookup({"order_number": "102500001", "email": EMAIL}, ctx=ctx, db=db) == NOT_AVAILABLE and db.calls == []


def test_user_text_is_only_ever_a_bound_parameter():
    db = FakeDB()
    lookup({"order_number": "102500001", "email": EMAIL}, db=db)
    for sql, _ in db.calls:
        assert "102500001" not in sql and "example" not in sql and "%s" in sql and "LIMIT" in sql


def test_item_list_is_capped():
    db = FakeDB()
    lookup({"order_number": "102500001", "email": EMAIL}, db=db)
    assert db.calls[1][1][1] == 25


# ---------- rate limit ----------
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_sixth_different_guess_is_refused_and_does_not_reach_the_database():
    clock, db = Clock(), FakeDB()
    lim = AttemptLimiter(clock=clock)
    for n in range(5):
        assert lookup({"order_number": f"99900000{n}", "email": EMAIL}, db=db, limiter=lim) == NO_MATCH
    calls_before = len(db.calls)
    assert lookup({"order_number": "102500001", "email": EMAIL}, db=db, limiter=lim) == RATE_LIMITED
    assert len(db.calls) == calls_before


def test_repeating_the_same_guess_is_not_a_new_attempt():
    lim = AttemptLimiter(clock=Clock())
    for _ in range(12):                                   # the model retrying one lookup must not burn the budget
        assert lookup({"order_number": "102500001", "email": EMAIL}, limiter=lim)["found"] is True


def test_window_expires_and_visitors_are_separate():
    clock = Clock()
    lim = AttemptLimiter(clock=clock)
    for n in range(5):
        lookup({"order_number": f"11{n}", "email": EMAIL}, limiter=lim)
    assert lookup({"order_number": "999", "email": EMAIL}, limiter=lim) == RATE_LIMITED
    assert lookup({"order_number": "999", "email": EMAIL}, ctx=ChatContext(client_id="5.6.7.8"), limiter=lim) == NO_MATCH
    clock.t = 15 * 60 + 1
    assert lookup({"order_number": "999", "email": EMAIL}, limiter=lim) == NO_MATCH


def test_limiter_keeps_no_plain_email_or_order_number():
    lim = AttemptLimiter()
    lookup({"order_number": "102500001", "email": EMAIL}, limiter=lim)
    dumped = str(lim._hits)
    assert EMAIL not in dumped and "102500001" not in dumped


def test_a_found_order_tells_the_model_it_cannot_cancel_or_change_orders():
    note = lookup({"order_number": "102500001", "email": EMAIL})["note"]
    assert "cannot cancel, change, refund or return" in note and "get_store_info" in note and "cannot be done in this chat" in note
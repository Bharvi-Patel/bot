"""Who is asking. Built by YOUR server code from the request, never from the model or from anything the customer typed.

store_uuid    which store this chat belongs to (the Store-UUID of the storefront that sent it). Set by the same
              server code from the real request, never from the message, so a customer cannot switch stores.
client_id     identifies the visitor for rate limiting (the real client IP or a signed session id).
customer_uuid set only after a real login (or a verified one-time code).
customer_token the logged-in customer's own login token, forwarded to the store API so the API itself decides which orders
              belong to that customer. It is a credential: it never reaches the model, a log line, a trace or a reply, and it
              is left out of repr() and comparisons so it cannot leak through an error message or a test failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatContext:
    store_uuid: str | None = None
    client_id: str | None = None
    customer_uuid: str | None = None
    customer_token: str | None = field(default=None, repr=False, compare=False)

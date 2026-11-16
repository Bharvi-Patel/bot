"""Who is asking. Built by YOUR server code from the request, never from the model or from anything the customer typed.

client_id     identifies the visitor for rate limiting (the real client IP or a signed session id).
customer_uuid set only after a real login (or a verified one-time code). Orders then match on it instead of the email.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatContext:
    client_id: str | None = None
    customer_uuid: str | None = None

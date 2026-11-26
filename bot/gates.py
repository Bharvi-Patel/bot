"""Checks made BEFORE the model sees a customer message. A match gets a fixed reply and no model call.

is_extraction_attempt: blunt attempts to read the bot's instructions, tools or database, or other customers' data.
is_order_change_request: requests to cancel, change or refund an order (the bot cannot, and must never imply it did).
"""
from __future__ import annotations

import re

# Blunt attempts to read the bot's instructions, tools or database. Checked BEFORE the model sees the message.
# Best effort, English only: a reworded or translated attempt still reaches the model and its own refusal.
_EXTRACTION = re.compile(
    r"\b(?:system|developer|hidden|secret|initial|original|internal)\s+(?:prompt|instructions?|message)\b"
    r"|\byour\s+(?:prompt|instructions|tools|functions|tool\s+calls|function\s+calls|parameters|schema|configuration)\b"
    r"|\bignore\s+(?:(?:all|any|your|the|previous|prior|above)\s+)*(?:instructions?|rules|prompt|guidelines)\b"
    r"|\b(?:developer|debug|admin|god|dan|jailbreak)\s+mode\b"
    r"|\bwhat\s+(?:tools|functions)\s+(?:can|do|are)\s+you\b"
    r"|\b(?:database|db|sql)\s+(?:tables?|schema|structure|columns?)\b"
    r"|\b(?:repeat|print|output)\s+(?:everything|all|the\s+(?:text|words))\s+(?:above|before)\b"
    # requests for other customers' data (emails, phones, orders, lists): refused before the model sees them
    r"|\b(?:e-?mails?|phone\s+numbers?|addresses|names|orders?|details|information|data|records|list)\s+(?:of|for|from)\s+(?:your|the|other|all|any|every)\s+(?:other\s+)?customers?\b"
    r"|\b(?:your|the|other|all)\s+customers?(?:'s|s')?\s+(?:e-?mails?|phone\s+numbers?|addresses|names|orders?|details|information|data|records)\b"
    r"|\bcustomer\s+(?:list|database|e-?mails?|data|records)\b", re.I)


def is_extraction_attempt(text: str) -> bool:
    return bool(_EXTRACTION.search(text))


ORDER_CHANGE_REPLY = ("I can't cancel, change or refund orders in this chat. Please contact the store directly and they can "
                      "help; ask me for their phone number or email if you need it.")
# Requests to cancel / change / refund an order. Answered with a fixed reply BEFORE the model sees them, so it can never
# imply the order was or will be cancelled. Policy questions ("what is your cancellation policy?") are NOT matched.
_VERB = r"(?:cancel|change|modify|edit|refund)"
_TARGET = r"(?:\s+\w+){0,4}?\s+(?:order|purchase)\b"
_ORDER_CHANGE = re.compile(
    rf"\b(?:can|could|may|would|will)\s+(?:i|you|we|someone)\s+(?:please\s+)?{_VERB}\b{_TARGET}"
    rf"|\b(?:i|we)(?:'d|\s+would)?\s+(?:want|need|wish|like|have)\s+to\s+{_VERB}\b{_TARGET}"
    rf"|^\s*(?:please\s+|pls\s+)?{_VERB}\b{_TARGET}", re.I)
_STATUS_WORDS = re.compile(r"\b(?:where|status|track|tracking|when)\b", re.I)


def is_order_change_request(text: str) -> bool:
    text = text.replace("\u2019", "'")
    return bool(_ORDER_CHANGE.search(text)) and not _STATUS_WORDS.search(text)   # mixed status+cancel messages go to the model

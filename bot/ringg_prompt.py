"""The prompt for a store's Ringg.AI agent (text chat and voice), built from the SAME rules the chat bot uses.

    python -m bot.ringg_prompt <store-uuid> [--channel text|voice]      prints the prompt: paste it into the agent's Prompt in Ringg

One source of truth: bot/prompt.py renders the store's rules (no invented prices, no discounts, contact details only from
get_store_info, no medical claims, shipping and pickup wording, ...). This module changes only what cannot apply on Ringg:

* Orders. The Ringg agent has no order tool: a browser or phone caller cannot prove who they are (the widget's variables are
  visible in the page source, so a "logged in" value from the page could be faked). The order rules are replaced by one line
  that sends the customer to their account or the store.
* Voice wording, for --channel voice: short spoken sentences, no reading out links, say prices as a person would. The shared
  rules that tell the assistant to give links or image URLs are reworded or dropped for voice, so they do not clash with
  "never read out web addresses". The text channel keeps them.

If bot/prompt.py changes so that an order rule or a link rule no longer matches what is changed here, this raises instead of
producing a prompt that talks about a tool the agent does not have, or tells a voice agent to read out links.
"""
from __future__ import annotations

import argparse
import re
import sys

from bot.context import ChatContext
from bot.prompt import render_system_prompt
from bot.stores import config_for

# Lines of the shared prompt that are about get_order_status, which the Ringg agent does not have.
_ORDER_RULE_STARTS = ("- Orders: use get_order_status", "- When an order is found", "- If it finds no order",
                      "- If a message mixes an order question")
_ORDERS_CANNOT = ("- Orders: you cannot look up orders here. If the customer asks about an order, tell them to log in to their account "
                  "on the website and check it there, or to contact the store, and give the store's phone or email from get_store_info. "
                  "Never state any order detail.")

VOICE_RULES = """Speaking rules (this is a phone or browser voice call):
- Speak in short, natural sentences, one idea at a time, and keep each answer under about 25 seconds.
- Say prices the way a person would ("five hundred ninety-nine dollars"). Read at most three products, then offer to continue.
- Never read out web addresses or long SKUs. Say the product name; if the customer needs a link or a SKU, say it is on the website.
- If you did not catch something, ask the customer to repeat it. Never guess a product name, a number or a spelling.
- Do not use lists, bullet points, markdown or emoji."""

# Shared rules that hand out links. They suit a chat window, not a phone call, so for voice each is reworded or dropped.
# (old text, voice text); every old text must be found exactly once, otherwise bot/prompt.py changed and this must be updated.
_VOICE_LINK_EDITS = (
    (", and link it only with a URL a tool returned.", "."),
    ("and give image links from the result as plain URLs.", "and describe the item from the result. If they want a picture, say it is on the website."),
    ("\n- Keep answers short and link the product or policy page.", ""),
    # the speaking rules say to read at most three products, so the shared "list up to 5" would clash on a phone call
    ("list up to 5 of the results", "read out up to 3 of the results"),
)

# The caller is already on a call with the store's assistant, so a voice agent does not volunteer a phone number or email address
# (reading one aloud is awkward, and the number a caller reached may be this very agent). Where the shared rules say to give the
# store's contact details, voice sends the caller to the store's website instead. If the caller asks for the phone number or email,
# the agent gives it, copied from get_store_info. Same rule as above: each old text is found exactly once.
_VOICE_CONTACT_EDITS = (
    ("say so and give the store's phone or email (get_store_info).", "say so and suggest checking the store's website."),
    ("and add contact details only if they do not fully answer it; do not reply with just a phone or email when the chunks answer the question.",
     "and, only if they do not fully answer it, say what you do not have; do not say you don't have the information when the chunks answer the question."),
    ("then give the store's phone or email. Never reply with contact details alone, and never word it",
     "then suggest checking the store's website. Never word it"),
    (" To give contact details, call get_store_info first and copy the phone or email from its result; never write an email address or phone number from memory.", ""),
    ("say you don't have that information and give the store's phone or email.", "say you don't have that information and suggest checking the store's website."),
    ("or to contact the store, and give the store's phone or email from get_store_info.", "or to contact the store through its website."),
    ("and give the store's phone or email so they can ask the store.", "and suggest they ask the store through its website."),
    ("\n- Give contact details as plain lines and do not add notes about where they came from.", ""),
    ("\n- Phone numbers and email addresses: copy them only from a get_store_info result. To give contact details, call get_store_info first. Never write one from memory.",
     "\n- Do not offer or volunteer the store's phone number or email address. Give one only when the caller asks for it: call "
     "get_store_info first and copy it from its result, never from memory. Read a phone number slowly in short groups of digits, and "
     "spell an email address out, saying at and dot. If get_store_info has none, say you don't have it and suggest checking the store's website."),
    ("say you don't have exact shipping prices and give the store's phone or email.", "say you don't have exact shipping prices and suggest checking the store's website."),
    ("say you don't have pickup details and give the store's phone or email;", "say you don't have pickup details and suggest checking the store's website;"),
    ("give the regular hours and the phone.", "give the regular hours."),
    (" Give the store's phone or email only when the customer asks for it or when you cannot answer. Never write an email address or phone number from your own knowledge.", ""),
    ("and give the store's phone or email in that same reply; do not ask whether the customer wants the contact details.",
     "and suggest checking the store's website in that same reply."),
)


def _voice_without_links(text: str) -> str:
    for old, new in _VOICE_LINK_EDITS:
        if text.count(old) != 1:
            raise RuntimeError("bot/prompt.py changed: a link rule is no longer what bot/ringg_prompt.py rewords for voice. Update it.")
        text = text.replace(old, new)
    for old, new in _VOICE_CONTACT_EDITS:
        if text.count(old) != 1:
            raise RuntimeError("bot/prompt.py changed: a contact-details rule is no longer what bot/ringg_prompt.py rewords for voice. Update it.")
        text = text.replace(old, new)
    return text


TEXT_RULES = """Chat style: keep answers short, plain text, and link a product or page only with a URL a tool returned."""


# Ringg writes an agent variable in prompt text as {{name}}. The shared prompt carries the store's name through the agent's
# store_name variable, which the storefront (or the outbound call) fills in for each call.
STORE_NAME_TOKEN = "{{store_name}}"


def _shared_config(vertical: str = "general"):
    from bot.store_config import StoreConfig
    return StoreConfig(store_uuid="shared", store_name=STORE_NAME_TOKEN, vertical=vertical)


# Ringg offers an on-call tool to its model only where the prompt mentions it as @||tool_name||; a plain name is just text.
ATTRIBUTE_VALUES_LINE = ("- To find which values a filter has (for example the colors or bed sizes among the matching products), use "
                         "list_attribute_values with the filter group and the product word, then pass the value to search_products.")


def with_tool_mentions(text: str) -> str:
    """Every voice tool named in the prompt becomes Ringg's @||name|| mention, and list_attribute_values, which the shared
    rules never name, gets one line so the agent is offered it too."""
    from bot.ringg import VOICE_TOOLS
    if "list_attribute_values" not in text:
        text = text.rstrip() + "\n" + ATTRIBUTE_VALUES_LINE + "\n"
    names = "|".join(sorted(VOICE_TOOLS, key=len, reverse=True))
    return re.sub(rf"(?<![\w|])({names})(?![\w|])", r"@||\1||", text)


def ringg_prompt(cfg=None, channel: str = "text", vertical: str = "general", tool_mentions: bool = False) -> str:
    if channel not in ("text", "voice"):
        raise ValueError("channel must be text or voice")
    if cfg is None:
        cfg = _shared_config(vertical=vertical)
    base = render_system_prompt(cfg)
    out, replaced = [], False
    for line in base.split("\n"):
        if line.startswith(_ORDER_RULE_STARTS):
            if not replaced:
                out.append(_ORDERS_CANNOT)
                replaced = True
            continue
        line = line.replace("store hours and orders.", "and store hours.")
        line = line.replace("unless get_order_status returned them in this turn, and never invent them", "and never invent them")
        out.append(line)
    text = "\n".join(out)
    if channel == "voice":
        text = _voice_without_links(text)
    if not replaced or "get_order_status" in text:
        raise RuntimeError("bot/prompt.py changed: the order rules are no longer what bot/ringg_prompt.py removes. Update it.")
    text = text.rstrip() + "\n\n" + (VOICE_RULES if channel == "voice" else TEXT_RULES) + "\n"
    return with_tool_mentions(text) if tool_mentions else text


def main(argv: list[str] | None = None) -> None:
    from bot.store_config import valid_store_id
    from bot.verticals import VERTICALS
    ap = argparse.ArgumentParser(description="Print the Ringg agent prompt (shared {{store_name}} by default, or for a specific store_uuid).")
    ap.add_argument("store_uuid", nargs="?", default=None, help="Optional store_uuid; omit to use {{store_name}} for a shared multi-store agent.")
    ap.add_argument("--channel", choices=("text", "voice"), default="text")
    ap.add_argument("--plain-tool-names", action="store_true",
                    help="Leave tool names as plain words. By default they are written as Ringg's @||tool|| mentions, which Ringg "
                         "needs to offer the agent its on-call tools.")
    ap.add_argument("--vertical", choices=sorted(VERTICALS), default="general",
                    help="Vertical rules for the shared {{store_name}} prompt (ignored when store_uuid is given).")
    args = ap.parse_args(argv)
    cfg = None
    if args.store_uuid is not None:
        if not valid_store_id(args.store_uuid):
            ap.error("store_uuid has characters that are not allowed")
        cfg = config_for(ChatContext(store_uuid=args.store_uuid))
    sys.stdout.write(ringg_prompt(cfg, args.channel, vertical=args.vertical, tool_mentions=not args.plain_tool_names))


if __name__ == "__main__":
    main()



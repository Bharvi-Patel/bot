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
)


def _voice_without_links(text: str) -> str:
    for old, new in _VOICE_LINK_EDITS:
        if text.count(old) != 1:
            raise RuntimeError("bot/prompt.py changed: a link rule is no longer what bot/ringg_prompt.py rewords for voice. Update it.")
        text = text.replace(old, new)
    return text


TEXT_RULES = """Chat style: keep answers short, plain text, and link a product or page only with a URL a tool returned."""


def _shared_config(vertical: str = "general"):
    from bot.store_config import StoreConfig
    return StoreConfig(store_uuid="shared", store_name="@store_name", vertical=vertical)


def ringg_prompt(cfg=None, channel: str = "text", vertical: str = "general") -> str:
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
    return text.rstrip() + "\n\n" + (VOICE_RULES if channel == "voice" else TEXT_RULES) + "\n"


def main(argv: list[str] | None = None) -> None:
    from bot.store_config import valid_store_id
    from bot.verticals import VERTICALS
    ap = argparse.ArgumentParser(description="Print the Ringg agent prompt (shared @store_name by default, or for a specific store_uuid).")
    ap.add_argument("store_uuid", nargs="?", default=None, help="Optional store_uuid; omit to use @store_name for a shared multi-store agent.")
    ap.add_argument("--channel", choices=("text", "voice"), default="text")
    ap.add_argument("--vertical", choices=sorted(VERTICALS), default="general",
                    help="Vertical rules for the shared @store_name prompt (ignored when store_uuid is given).")
    args = ap.parse_args(argv)
    cfg = None
    if args.store_uuid is not None:
        if not valid_store_id(args.store_uuid):
            ap.error("store_uuid has characters that are not allowed")
        cfg = config_for(ChatContext(store_uuid=args.store_uuid))
    sys.stdout.write(ringg_prompt(cfg, args.channel, vertical=args.vertical))


if __name__ == "__main__":
    main()



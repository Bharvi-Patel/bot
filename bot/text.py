"""Turn catalog HTML text into short plain text for the model."""
from __future__ import annotations

import html
import re

_SCRIPT_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_BREAKS = re.compile(r"<\s*(br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr)\b[^>]*>", re.I)
_LI = re.compile(r"<\s*li\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]+>")


def clean_text(raw: str | None, limit: int) -> str | None:
    """Strip HTML, decode entities, collapse whitespace and cut to `limit` characters on a word boundary."""
    if not raw:
        return None
    s = _SCRIPT_STYLE.sub(" ", raw)
    s = _LI.sub("\n- ", s)
    s = _BREAKS.sub("\n", s)
    s = _TAGS.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v\xa0]+", " ", s)
    s = re.sub(r"\n\s*", "\n", s).strip()
    if not s:
        return None
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",.;:-") + "..."
    return s

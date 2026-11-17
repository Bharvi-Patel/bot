"""get_store_info: public store details (contact, address, hours). Reads the store API (GET /stores) through
bot.api_client, which passes on only name, phone, email, address, hours and timezone, never the store's settings or keys.

opening_hours is stored as JSON like {"monday": {"open": "09:00", "close": "19:00", "is_open": true}, ...}
with the days in alphabetical order. We turn it into Monday-first plain lines in 12-hour time, add a
one-line summary, and work out "open right now" in the store's own timezone.
"""
from __future__ import annotations

import json
from datetime import datetime, time
import re
from typing import Any

from bot.api_client import ApiClient, ApiError, get_client

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_json(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)


def _to_time(value: Any) -> time | None:
    try:
        h, m = str(value).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


def _fmt(t: time) -> str:
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def _day_text(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "not listed"
    if entry.get("is_open") is False:
        return "Closed"
    o, c = _to_time(entry.get("open")), _to_time(entry.get("close"))
    if o is None or c is None:
        return "not listed"
    return f"{_fmt(o)} - {_fmt(c)}"


def format_hours(hours: dict) -> tuple[list[dict], str]:
    """Return (one line per day Monday..Sunday, a short summary that merges consecutive identical days)."""
    lines = [{"day": d.capitalize(), "hours": _day_text(hours.get(d))} for d in DAYS]
    groups: list[list] = []  # [first_day, last_day, text]
    for line in lines:
        if groups and groups[-1][2] == line["hours"]:
            groups[-1][1] = line["day"]
        else:
            groups.append([line["day"], line["day"], line["hours"]])
    parts = []
    for first, last, text in groups:
        label = first if first == last else f"{first} to {last}"
        parts.append(f"{label}: {text}")
    return lines, "; ".join(parts)


def open_now(hours: dict, tz_name: str | None, now: datetime | None = None) -> dict | None:
    """Is the store open right now in its own timezone? None when it cannot be worked out."""
    if not tz_name or ZoneInfo is None:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        return None
    local = (now.astimezone(zone) if now else datetime.now(zone))
    entry = hours.get(DAYS[local.weekday()])
    if not isinstance(entry, dict):
        return None
    result: dict[str, Any] = {"local_time": local.strftime("%A %I:%M %p").replace(" 0", " ")}
    if entry.get("is_open") is False:
        result["is_open"] = False
        return result
    o, c = _to_time(entry.get("open")), _to_time(entry.get("close"))
    if o is None or c is None:
        return None
    t = local.time()
    result["is_open"] = (o <= t < c) if o < c else (t >= o or t < c)  # second form: closes after midnight
    return result


def _address(info: dict[str, Any]) -> str | None:
    """The API's address field already ends with city, state and zip for the saved store; tidy its spacing
    ("Jackson , TN"). If the zip is not in it, add city, state and zip so the address is complete either way."""
    parts = [str(info.get(k)).strip() for k in ("address", "address2") if info.get(k)]
    text = re.sub(r"\s+,", ",", ", ".join(p for p in parts if p))
    zipcode = str(info.get("zipcode") or "").strip()
    if zipcode and zipcode not in text:
        text = ", ".join(x for x in (text, info.get("city"), info.get("state"), zipcode) if x)
    return re.sub(r"\s+", " ", text).strip(" ,") or None


def get_store_info(args: dict[str, Any], client: ApiClient | None = None, now: datetime | None = None, ctx=None) -> dict[str, Any]:
    if args:
        return {"error": "this tool takes no parameters"}
    try:
        info = (client or get_client(ctx)).store_info()
    except ApiError:
        return {"error": "Store details are not available right now. Do not guess a phone number, email, address or hours."}
    if not info.get("name") and not info.get("phone") and not info.get("email"):
        return {"found": False, "note": "No store details are on file. Do not guess contact details."}

    store: dict[str, Any] = {
        "name": info.get("name"),
        "phone": info.get("phone") or None,
        "email": info.get("email") or None,
        "address": _address(info),
        "timezone": info.get("timezone") or None,
    }
    hours = info.get("opening_hours")
    if isinstance(hours, dict) and any(d in hours for d in DAYS):
        store["hours"], store["hours_summary"] = format_hours(hours)
        status = open_now(hours, store["timezone"], now)
        if status is not None:
            store["open_now"] = status
    else:
        store["hours"] = None
    return {
        "found": True,
        "stores": [store],
        "note": (
            "Quote hours exactly as given. Use open_now only for 'are you open right now?' questions, "
            "and mention the store's local time. Holidays are not in this data. If hours or a phone number "
            "are missing, say so instead of guessing."
        ),
    }

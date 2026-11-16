"""get_store_info: public store details (contact, address, hours). Reads vw_chat_store only,
which leaves out email settings and other configuration.

opening_hours is stored as JSON like {"monday": {"open": "09:00", "close": "19:00", "is_open": true}, ...}
with the days in alphabetical order. We turn it into Monday-first plain lines in 12-hour time, add a
one-line summary, and work out "open right now" in the store's own timezone.
"""
from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any, Callable

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


def get_store_info(args: dict[str, Any], run_query: Callable | None = None, now: datetime | None = None) -> dict[str, Any]:
    if args:
        return {"error": "this tool takes no parameters"}
    if run_query is None:
        from sjbot.db import run_query as default_run_query
        run_query = default_run_query
    rows = run_query(
        "SELECT name, phone, email, address, address2, city, state, zipcode, timezone, opening_hours "
        "FROM vw_chat_store LIMIT 3"
    )
    if not rows:
        return {"found": False, "note": "No store details are on file. Do not guess contact details."}

    stores = []
    for r in rows:
        address = ", ".join(x for x in (r["address"], r["address2"], r["city"], r["state"], r["zipcode"]) if x)
        store: dict[str, Any] = {
            "name": r["name"],
            "phone": r["phone"] or None,
            "email": r["email"] or None,
            "address": address or None,
            "timezone": r["timezone"] or None,
        }
        hours = _parse_json(r["opening_hours"])
        if isinstance(hours, dict) and any(d in hours for d in DAYS):
            store["hours"], store["hours_summary"] = format_hours(hours)
            status = open_now(hours, r["timezone"], now)
            if status is not None:
                store["open_now"] = status
        else:
            store["hours"] = None
            store["hours_text"] = hours if isinstance(hours, str) else None
        stores.append(store)
    return {
        "found": True,
        "stores": stores,
        "note": (
            "Quote hours exactly as given. Use open_now only for 'are you open right now?' questions, "
            "and mention the store's local time. Holidays are not in this data. If hours or a phone number "
            "are missing, say so instead of guessing."
        ),
    }

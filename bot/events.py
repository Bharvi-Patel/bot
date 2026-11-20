"""Funnel events: one small record per chat turn, for the dashboard (searches, product views, policy questions, orders, failures).

PRIVACY, on purpose: an event holds NO customer text. No message, history or reply; no search keyword; no product, order or
customer detail; no raw client id or IP. It holds only counts, tool names, yes/no outcomes and catalog slugs that a tool has
already accepted (so a slug can never be free text). Whether the dashboard needs the question text is a decision for the
client; until it is made, nothing here stores it, and adding it would be a new field with its own consent and retention.

Flow: the HTTP service opens collect() around one chat turn; the router reports each tool call it ran with record_tool();
the service then builds one event with build_chat_event() and hands it to a sink. Outside the service (the CLI, the evals)
there is no collector, so record_tool() does nothing.

Storage: JsonlEventSink appends to one file per UTC day, events-YYYY-MM-DD.jsonl, in BOT_EVENTS_DIR, and deletes files older
than BOT_EVENTS_RETENTION_DAYS (default 90). With BOT_EVENTS_DIR unset, events are off. A write problem is logged and never
reaches the customer.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Protocol

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90

_TOOL_NAME = re.compile(r"[a-z_]{1,40}")
_CODE = re.compile(r"[a-z_]{1,40}")                     # machine codes such as unknown_category; anything else becomes "other"
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
_ID = re.compile(r"[A-Za-z0-9._\-]{1,64}")
_OUTCOMES = ("answered", "blocked", "error")
MAX_TOOL_EVENTS = 12                                    # the router allows only a few calls per turn; this is a safety bound


# ---------- collecting what the router did during one turn ----------
_collector: ContextVar[list[dict] | None] = ContextVar("bot_event_collector", default=None)


def is_collecting() -> bool:
    """True inside collect() (i.e. an active HTTP service turn)."""
    return _collector.get() is not None


@contextmanager
def collect() -> Iterator[list[dict]]:
    """Everything record_tool() sees inside this block is appended to the yielded list (in the same thread/context)."""
    events: list[dict] = []
    token = _collector.set(events)
    try:
        yield events
    finally:
        _collector.reset(token)


def record_tool(name: str, args: Any, result: Any) -> None:
    """Called by the router after each tool it actually ran. No-op unless collect() is active. Never raises."""
    events = _collector.get()
    if events is None or len(events) >= MAX_TOOL_EVENTS:
        return
    try:
        events.append(summarize_tool(name, args, result))
    except Exception:                                   # an event must never break a chat
        log.debug("could not summarize tool call", exc_info=True)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000 else 0


def _slug(value: Any) -> str | None:
    return value if isinstance(value, str) and _SLUG.fullmatch(value) else None


def summarize_tool(name: str, args: Any, result: Any) -> dict[str, Any]:
    """The allowlisted facts about one tool call. Argument values and result contents are never copied, only counted."""
    args = args if isinstance(args, dict) else {}
    result = result if isinstance(result, dict) else {}
    ev: dict[str, Any] = {"name": name if isinstance(name, str) and _TOOL_NAME.fullmatch(name) else "other"}
    err = result.get("error")
    ev["ok"] = "error" not in result
    if not ev["ok"]:
        ev["error"] = err if isinstance(err, str) and _CODE.fullmatch(err) else "other"

    if name == "search_products":
        kinds = []
        if args.get("keyword"):
            kinds.append("keyword")                     # the fact that a keyword was used, never its text
        if args.get("category_slug"):
            kinds.append("category")
        if args.get("brand_slug"):
            kinds.append("brand")
        if args.get("min_price") is not None or args.get("max_price") is not None:
            kinds.append("price")
        if args.get("attribute_group") or args.get("color") or args.get("material"):
            kinds.append("attribute")
        if args.get("sort") in ("price_asc", "price_desc"):
            kinds.append("sort")
        ev["filters"] = kinds
        if ev["ok"]:
            ev["results"] = _count(result.get("count"))
            for key, field in (("category", "category_slug"), ("brand", "brand_slug")):
                if (slug := _slug(args.get(field))):
                    ev[key] = slug                      # only after the tool accepted it, so it is a real catalog slug
    elif name == "recommend_products":
        kinds = []
        if args.get("category_slug"):
            kinds.append("category")
        if args.get("brand_slug"):
            kinds.append("brand")
        if args.get("min_price") is not None or args.get("max_price") is not None:
            kinds.append("price")
        ev["filters"] = kinds
        if ev["ok"]:
            ev["found"] = result.get("found") is True
            products = result.get("products")
            ev["results"] = _count(len(products)) if isinstance(products, list) else 0
            for key, field in (("category", "category_slug"), ("brand", "brand_slug")):
                if (slug := _slug(args.get(field))):
                    ev[key] = slug
    elif name == "get_product_details" and ev["ok"]:
        ev["found"] = result.get("found") is True
    elif name in ("search_policies", "get_shipping_options") and ev["ok"]:
        ev["found"] = result.get("found") is True
        ev["results"] = _count(len(result["chunks"])) if isinstance(result.get("chunks"), list) else 0
    elif name == "get_order_status" and ev["ok"]:
        ev["found"] = result.get("found") is True
        orders = result.get("orders")
        ev["results"] = _count(len(orders)) if isinstance(orders, list) else (1 if ev["found"] else 0)
    return ev


# ---------- the event ----------
def build_chat_event(*, request_id: str | None, store: str, client: str | None, conversation: str | None, logged_in: bool,
                     outcome: str, blocked: str | None, ms: int, tool_events: list[dict],
                     now: datetime | None = None) -> dict[str, Any]:
    """One chat turn. Every field is checked, so a bad value is dropped or replaced, never written as given."""
    now = now or datetime.now(timezone.utc)
    return {
        "v": SCHEMA_VERSION,
        "event": "chat_turn",
        "ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request_id": request_id if isinstance(request_id, str) and _ID.fullmatch(request_id) else None,
        "store": store if isinstance(store, str) and _ID.fullmatch(store) else None,
        "client": client if isinstance(client, str) and re.fullmatch(r"[0-9a-f]{12}", client) else None,
        "conversation": conversation if isinstance(conversation, str) and _ID.fullmatch(conversation) else None,
        "logged_in": bool(logged_in),
        "outcome": outcome if outcome in _OUTCOMES else "error",
        "blocked": blocked if isinstance(blocked, str) and _CODE.fullmatch(blocked) else None,
        "ms": _count(ms),
        "tools": [t for t in tool_events[:MAX_TOOL_EVENTS] if isinstance(t, dict)],
    }


# ---------- sinks ----------
class EventSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


class NullEventSink:
    """Events are off (BOT_EVENTS_DIR is not set)."""
    enabled = False

    def write(self, event: dict[str, Any]) -> None:
        return None


_FILE = re.compile(r"events-(\d{4})-(\d{2})-(\d{2})\.jsonl")


class JsonlEventSink:
    """Appends one JSON line per event to events-YYYY-MM-DD.jsonl (UTC date) and prunes files past the retention period.
    Thread-safe within one process. Only files with exactly that name pattern are ever deleted."""
    enabled = True

    def __init__(self, directory: str | os.PathLike, retention_days: int = DEFAULT_RETENTION_DAYS,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if retention_days < 1:
            raise ValueError("retention_days must be 1 or more")
        self.dir = pathlib.Path(directory)
        self.retention_days, self._clock = retention_days, clock
        self._lock = threading.Lock()
        self._pruned_on: date | None = None
        self._failures = 0
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.prune()

    def prune(self) -> int:
        """Delete event files older than the retention period (by the date in the file name). Returns how many."""
        today = self._clock().astimezone(timezone.utc).date()
        cutoff = today - timedelta(days=self.retention_days)
        removed = 0
        with self._lock:
            self._pruned_on = today
            for path in self.dir.iterdir():
                m = _FILE.fullmatch(path.name)
                if not m or not path.is_file():
                    continue
                try:
                    if date(int(m[1]), int(m[2]), int(m[3])) < cutoff:
                        path.unlink()
                        removed += 1
                except (ValueError, OSError):
                    log.warning("could not prune %s", path.name)
        return removed

    def write(self, event: dict[str, Any]) -> None:
        try:
            today = self._clock().astimezone(timezone.utc).date()
            if self._pruned_on != today:
                self.prune()
            line = json.dumps(event, separators=(",", ":"), ensure_ascii=True) + "\n"
            path = self.dir / f"events-{today.isoformat()}.jsonl"
            with self._lock:
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf8") as f:
                    f.write(line)
        except Exception:
            self._failures += 1
            if self._failures == 1 or self._failures % 100 == 0:
                log.warning("could not write funnel event (%d failures so far)", self._failures, exc_info=True)


def sink_from_env() -> EventSink:
    """BOT_EVENTS_DIR (where to write) and BOT_EVENTS_RETENTION_DAYS (default 90). Unset directory = events off."""
    directory = (os.getenv("BOT_EVENTS_DIR") or "").strip()
    if not directory:
        log.info("funnel events are off: BOT_EVENTS_DIR is not set")
        return NullEventSink()
    raw = (os.getenv("BOT_EVENTS_RETENTION_DAYS") or "").strip()
    if raw and (not raw.isdigit() or int(raw) < 1):
        raise ValueError(f"BOT_EVENTS_RETENTION_DAYS must be a whole number of 1 or more, got {raw!r}")
    return JsonlEventSink(directory, int(raw) if raw else DEFAULT_RETENTION_DAYS)

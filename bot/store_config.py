"""Per-store settings: which store this is, how it is described, and whether the assistant is switched on.

A StoreConfig holds only what is NOT already served by the store's API (phone, email, address and hours come from
get_store_info). Settings come from a ConfigSource; today that is a folder of JSON files, one per store UUID, so the
source can later be swapped for the API's module settings without touching the rest of the bot.

A store with no config is NOT served (fail closed): the bot never falls back to another store's settings.

JSON file format (file name = the store UUID + ".json"):
    {"store_name": "Acme Furniture", "allowed_domains": ["acmefurniture.com"], "vertical": "furniture",
     "enabled": true, "shipping_example": "We ship only within the USA", "catalog_rules": ["..."]}
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from bot.verticals import VERTICALS

STORE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '-]{0,38}[A-Za-z0-9]$|^[A-Za-z0-9]$")
MAX_RULE_CHARS = 600
MAX_RULES = 10


class StoreNotConfigured(Exception):
    """No usable config for this store. The caller should tell the visitor the assistant is unavailable."""


class ConfigError(ValueError):
    """A config file or value is not valid."""


def valid_store_id(store_uuid: Any) -> bool:
    return isinstance(store_uuid, str) and bool(STORE_ID_RE.match(store_uuid))


def resolve_store_uuid(ctx: Any = None) -> str | None:
    """The store this chat belongs to: ctx.store_uuid, or BOT_STORE_UUID for local development and single-store setups.
    None when neither is set or the id is not valid. Nothing else ever decides which store's data a tool reads."""
    import os
    sid = getattr(ctx, "store_uuid", None) or os.getenv("BOT_STORE_UUID")
    return sid if valid_store_id(sid) else None


def service_key_from_env() -> str | dict[str, str]:
    """BOT_SERVICE_KEY: either one shared secret string, or a JSON object {"<store_uuid>": "<secret>"} for
    per-store service keys. Unset = service refuses every chat."""
    import os
    raw = os.getenv("BOT_SERVICE_KEY", "").strip()
    if not raw or not raw.startswith("{"):
        return raw
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"BOT_SERVICE_KEY must be a secret string or a JSON object: {exc}") from None
    if not isinstance(data, dict) or not data:
        raise ValueError("BOT_SERVICE_KEY JSON must be a non-empty object of store_uuid -> secret")
    out: dict[str, str] = {}
    for store, secret in data.items():
        if not valid_store_id(store) or not isinstance(secret, str) or not secret.strip():
            raise ValueError("BOT_SERVICE_KEY has an invalid store_uuid or empty secret")
        out[store] = secret.strip()
    return out


def service_key_ok(received: str | None, store_uuid: str | None, expected: str | dict[str, str]) -> bool:
    """Constant-time check of X-Bot-Key against either a shared key or a per-store key map."""
    import hmac
    if isinstance(expected, dict):
        if not valid_store_id(store_uuid):
            return False
        target = expected.get(store_uuid) or ""
        return bool(target) and bool(received) and hmac.compare_digest(received.encode(), target.encode())
    return bool(expected) and bool(received) and hmac.compare_digest(received.encode(), expected.encode())


def _one_line(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    if "\n" in value or "\r" in value or "<<" in value:
        raise ConfigError(f"{field} must be one line and must not contain '<<'")
    if len(value) > limit:
        raise ConfigError(f"{field} is longer than {limit} characters")
    return value.strip()


@dataclass(frozen=True)
class StoreConfig:
    store_uuid: str
    store_name: str
    allowed_domains: tuple[str, ...] = ()
    vertical: str = "general"
    enabled: bool = True
    shipping_example: str = "We do not ship to that location"
    catalog_rules: tuple[str, ...] = ()      # store-specific catalog quirks, one sentence or two per rule
    scrub_terms: tuple[str, ...] = ()        # words (such as another showroom's city) whose sentences are removed from page text

    def __post_init__(self) -> None:
        if not valid_store_id(self.store_uuid):
            raise ConfigError("store_uuid has characters that are not allowed")
        object.__setattr__(self, "store_name", _one_line(self.store_name, "store_name", 80))
        object.__setattr__(self, "shipping_example", _one_line(self.shipping_example, "shipping_example", 200))
        if self.vertical not in VERTICALS:
            raise ConfigError(f"unknown vertical {self.vertical!r}; known: {', '.join(sorted(VERTICALS))}")
        domains = tuple(str(d).strip().lower().removeprefix("www.") for d in self.allowed_domains)
        for d in domains:
            if not _HOST_RE.match(d):
                raise ConfigError(f"allowed_domains has an invalid host name: {d!r}")
        object.__setattr__(self, "allowed_domains", domains)
        rules = tuple(self.catalog_rules)
        if len(rules) > MAX_RULES:
            raise ConfigError(f"at most {MAX_RULES} catalog_rules")
        object.__setattr__(self, "catalog_rules", tuple(_one_line(r, "catalog_rules", MAX_RULE_CHARS) for r in rules))
        if not isinstance(self.enabled, bool):
            raise ConfigError("enabled must be true or false")
        terms = tuple(self.scrub_terms)
        if len(terms) > 20:
            raise ConfigError("at most 20 scrub_terms")
        for t in terms:
            if not isinstance(t, str) or not _TERM_RE.match(t):
                raise ConfigError(f"scrub_terms must be plain words or phrases (letters, digits, spaces, hyphens): {t!r}")
        object.__setattr__(self, "scrub_terms", tuple(t.strip() for t in terms))

    @classmethod
    def from_dict(cls, store_uuid: str, data: dict[str, Any]) -> "StoreConfig":
        known = {"store_name", "allowed_domains", "vertical", "enabled", "shipping_example", "catalog_rules", "scrub_terms"}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        kwargs = {k: data[k] for k in known if k in data}
        for key in ("allowed_domains", "catalog_rules", "scrub_terms"):
            if key in kwargs:
                if not isinstance(kwargs[key], (list, tuple)):
                    raise ConfigError(f"{key} must be a list")
                kwargs[key] = tuple(kwargs[key])
        return cls(store_uuid=store_uuid, **kwargs)


class ConfigSource(Protocol):
    def get(self, store_uuid: str) -> StoreConfig | None: ...


class DictSource:
    """Settings held in memory (tests and tiny setups)."""

    def __init__(self, configs: dict[str, StoreConfig]):
        self._configs = dict(configs)

    def get(self, store_uuid: str) -> StoreConfig | None:
        return self._configs.get(store_uuid)


class JsonDirSource:
    """One JSON file per store: <folder>/<store_uuid>.json."""

    def __init__(self, folder: str | pathlib.Path):
        self._folder = pathlib.Path(folder)

    def get(self, store_uuid: str) -> StoreConfig | None:
        if not valid_store_id(store_uuid):          # the id becomes part of a file name
            return None
        path = self._folder / f"{store_uuid}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path.name} must hold a JSON object")
        return StoreConfig.from_dict(store_uuid, data)


class StoreConfigs:
    """Looks settings up from a source and keeps each answer for ttl seconds, so a changed file is picked up
    without a restart. A missing or disabled store raises StoreNotConfigured."""

    def __init__(self, source: ConfigSource, ttl: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._source, self._ttl, self._clock = source, ttl, clock
        self._cache: dict[str, tuple[float, StoreConfig | None]] = {}

    def for_store(self, store_uuid: str | None) -> StoreConfig:
        if not valid_store_id(store_uuid):
            raise StoreNotConfigured("no valid store id")
        now = self._clock()
        hit = self._cache.get(store_uuid)
        if hit is None or now - hit[0] >= self._ttl:
            hit = (now, self._source.get(store_uuid))
            self._cache[store_uuid] = hit
        cfg = hit[1]
        if cfg is None:
            raise StoreNotConfigured(f"store {store_uuid} has no assistant settings")
        if not cfg.enabled:
            raise StoreNotConfigured(f"the assistant is switched off for store {store_uuid}")
        return cfg

    def for_ctx(self, ctx: Any) -> StoreConfig:
        return self.for_store(getattr(ctx, "store_uuid", None))

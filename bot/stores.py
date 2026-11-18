"""Which settings a chat runs with.

On a multi-store server set BOT_STORE_CONFIG_DIR to a folder with one <store uuid>.json per store (format in
bot/store_config.py). Every chat is then answered with ITS store's settings, and a store with no settings, or one
switched off, gets UNAVAILABLE_REPLY and no model call.

With BOT_STORE_CONFIG_DIR unset (local development, chat_cli, evals) every chat uses the South Jackson development
settings from bot/dev_defaults.py. Do not run a multi-store server that way.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from bot.dev_defaults import SOUTH_JACKSON
from bot.store_config import ConfigError, JsonDirSource, StoreConfig, StoreConfigs, StoreNotConfigured

log = logging.getLogger(__name__)

UNAVAILABLE_REPLY = "The shopping assistant isn't available right now. Please contact the store directly."

_CONFIGS: StoreConfigs | None = None
_FOLDER: str | None = None
_WARNED = False


def reset() -> None:
    """Forget the loaded settings (tests, or after changing the environment)."""
    global _CONFIGS, _FOLDER, _WARNED
    _CONFIGS, _FOLDER, _WARNED = None, None, False


def _configs() -> StoreConfigs | None:
    global _CONFIGS, _FOLDER
    folder = os.getenv("BOT_STORE_CONFIG_DIR")
    if not folder:
        return None
    if _CONFIGS is None or folder != _FOLDER:
        ttl = float(os.getenv("BOT_STORE_CONFIG_TTL", "60"))
        _CONFIGS, _FOLDER = StoreConfigs(JsonDirSource(folder), ttl=ttl), folder
    return _CONFIGS


def is_multi_store() -> bool:
    """True when BOT_STORE_CONFIG_DIR is set, so each chat gets its own store's settings. False means every chat would
    use the South Jackson development settings (see config_for)."""
    return _configs() is not None


def config_for(ctx: Any) -> StoreConfig:
    """The settings for this chat's store. Raises StoreNotConfigured when the chat must not be answered."""
    global _WARNED
    configs = _configs()
    if configs is None:
        if not _WARNED:
            log.warning("BOT_STORE_CONFIG_DIR is not set: every chat uses the South Jackson development settings")
            _WARNED = True
        return SOUTH_JACKSON
    try:
        return configs.for_ctx(ctx)
    except ConfigError as exc:                      # a broken settings file must not take a store's answers down silently
        log.error("store settings are not valid: %s", exc)
        raise StoreNotConfigured(str(exc)) from exc

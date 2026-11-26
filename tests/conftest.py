"""Keep the tests independent of the developer's .env.

bot/__init__.py loads .env, so BOT_STORE_CONFIG_DIR set there would switch every router test into
multi-store mode, where a chat with no store id is refused. Tests that need it set it themselves."""
import pytest

# Load .env now (bot/__init__.py, and bot/server.py again on import), so a test file that imports either lazily cannot bring
# BOT_STORE_CONFIG_DIR back after the fixture below removed it.
import bot  # noqa: F401
import bot.server  # noqa: F401


@pytest.fixture(autouse=True)
def _no_store_config_dir_from_dotenv(monkeypatch):
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)

"""Keep the tests independent of the developer's .env.

bot/__init__.py loads .env, so BOT_STORE_CONFIG_DIR set there would switch every router test into
multi-store mode, where a chat with no store id is refused. Tests that need it set it themselves."""
import pytest


@pytest.fixture(autouse=True)
def _no_store_config_dir_from_dotenv(monkeypatch):
    monkeypatch.delenv("BOT_STORE_CONFIG_DIR", raising=False)

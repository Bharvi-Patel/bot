"""rag/ingest_store.py: the per-store runner. Its product step used to call a client method that does not exist."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rag"))

import ingest_store  # noqa: E402


class _FakeClient:
    def categories(self):
        return [{"slug": "living-room", "children": [{"slug": "living-room-sofas"}]},
                {"slug": "bedroom", "children": []}, {"slug": None}, "junk"]


def test_root_category_slugs_uses_the_clients_category_tree():
    assert ingest_store.root_category_slugs("store-a", client=_FakeClient()) == ["living-room", "bedroom"]


def test_the_runner_only_calls_client_methods_that_exist():
    from bot.api_client import ApiClient
    assert callable(getattr(ApiClient, "categories"))
    assert not hasattr(ApiClient, "category_tree")          # the name the runner used to call by mistake

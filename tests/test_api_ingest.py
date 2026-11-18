"""rag/api_ingest.py builds the policy-index rows from the storefront API, for one store, with no database."""
import hashlib

import pytest

import rag.api_ingest as ai
import rag.sync as sync


class FakeClient:
    def __init__(self, faqs):
        self._faqs = faqs

    def faqs(self):
        return self._faqs


FAQS = [
    {"slug": "payment", "question": "What payment methods do you accept?",
     "answer_html": "<p>We accept cards and <b>cash</b> for eligible orders.<br></p>", "sort_order": 1},
    {"slug": "empty", "question": "Empty answer?", "answer_html": "<p> </p>", "sort_order": 2},
]


def test_faq_rows_have_the_index_row_format_and_skip_empty_answers():
    rows = ai.faq_rows(FakeClient(FAQS))
    assert len(rows) == 1
    r = rows[0]
    assert r["source_type"] == "faq" and r["source_key"] == "payment" and r["url_path"] == "/faqs"
    assert r["title"] == r["heading"] == "What payment methods do you accept?"
    assert r["content"] == "What payment methods do you accept?\n\nWe accept cards and cash for eligible orders."
    assert r["content_hash"] == hashlib.sha256(r["content"].encode()).hexdigest()
    assert r["embedding"] is None and r["price"] is None and r["brand_slug"] is None


def test_ingest_only_offers_types_whose_api_shape_has_been_seen():
    assert len(ai.ingest(FakeClient(FAQS), ("faq",))) == 1
    for bad in ("page", "blog", "policy"):
        with pytest.raises(ValueError):
            ai.ingest(FakeClient(FAQS), (bad,))


def test_the_ingest_never_uses_the_database():
    import pathlib
    text = pathlib.Path(ai.__file__).read_text(encoding="utf8")
    assert "bot.db" not in text and "vw_chat" not in text and "mysql" not in text.lower()


def test_a_types_run_syncs_only_those_types_so_other_rows_are_kept():
    rows = [{"source_type": "faq", "source_key": "a"}, {"source_type": "policy", "source_key": "returns"}]
    assert sync.rows_for_types(rows, ["faq"]) == [rows[0]]
    new, gone, _ = sync.plan_changes(sync.rows_for_types([dict(r, content_hash="h") for r in rows], ["faq"]), {("a", "h")})
    assert new == [] and gone == []                      # the policy row is neither added nor, being outside the run, removed

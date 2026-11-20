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
    for bad in ("policy", "product"):
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


# ---------- pages ----------
import json
from pathlib import Path

PAGE_HTML = json.loads((Path(__file__).parent / "fixtures" / "api" / "page_mattress_buying_guide.json").read_text())[
    "page_languages"][0]["long_description"]
SJ = {"name": "South Jackson Furniture | Jackson | TN", "phone": "(731) 423-6565", "email": "southjacksonfurniture@gmail.com"}


def page(slug, html="<h2>Heading</h2><p>Some words.</p>", status=1, deleted=False, advanced=False):
    return {"slug": slug, "title": slug.replace("-", " ").title(), "html": html, "status": status, "deleted": deleted,
            "updated_at": "2026-06-07", "advanced": advanced}


class PageClient:
    def __init__(self, pages, store=SJ):
        self._pages = {p["slug"]: p for p in pages}
        self._store = store
        self.fetched = []

    def store_info(self):
        return self._store

    def store_pages(self):
        return [{"slug": s, "title": p["title"], "status": p["status"]} for s, p in self._pages.items()]

    def page(self, slug):
        self.fetched.append(slug)
        return self._pages[slug]


@pytest.mark.parametrize("slug,expected", [
    ("returns", "policy"), ("shipping-delivery", "policy"), ("warranty-information", "policy"), ("terms-and-conditions", "policy"),
    ("privacy-policy", "policy"), ("financing-by-lendpro", "policy"), ("care-and-cleaning", "guide"),
    ("mattress-buying-guide", "guide"), ("about-us", "guide"), ("contact-us", "guide"),
    ("home-page", None), ("promotions", None), ("sitemap", None), ("nectar", None), ("random-landing", None)])
def test_pages_are_classified_by_slug_and_unknown_ones_are_not_indexed(slug, expected):
    assert ai.classify_page(slug) == expected


def test_an_override_wins_even_for_a_never_indexed_slug():
    assert ai.classify_page("nectar", {"nectar": "brand_info"}) == "brand_info"
    assert ai.classify_page("home-page", {"home-page": "guide"}) == "guide"


def test_the_real_mattress_guide_becomes_guide_chunks_in_the_index_row_format():
    skipped, warnings = [], []
    rows = ai.page_rows(PageClient([page("mattress-buying-guide", PAGE_HTML)]), skipped=skipped, warnings=warnings)
    assert len(rows) == 3 and skipped == [] and warnings == []          # the demo data in this page IS this store's own data
    assert {r["source_type"] for r in rows} == {"guide"} and {r["source_key"] for r in rows} == {"mattress-buying-guide"}
    assert all(r["url_path"] == "/mattress-buying-guide" and r["updated_at"] == "2026-06-07" for r in rows)
    assert rows[0]["heading"] == "Mattress Buying Guide" and rows[1]["heading"] == "Mattress Buying Guide > Choosing the right firmness"
    for r in rows:
        assert r["content"].startswith(r["heading"]) and r["content_hash"] == hashlib.sha256(r["content"].encode()).hexdigest()
        assert r["embedding"] is None and "<" not in r["content"]
    assert "Memory Foam Mattresses" in " ".join(r["content"] for r in rows)


def test_every_page_left_out_is_reported_with_its_reason():
    skipped = []
    client = PageClient([page("home-page"), page("returns", status=0), page("contact-us", deleted=True),
                         page("care-and-cleaning", html="  "), page("shipping-delivery", advanced=True, html=""),
                         page("about-us", html="<p> </p>")])
    assert ai.page_rows(client, skipped=skipped) == []
    reasons = dict(skipped)
    assert "not recognised" in reasons["home-page"] and "not live" in reasons["returns"]
    assert "deleted" in reasons["contact-us"] and reasons["care-and-cleaning"] == "no text"
    assert "page-builder" in reasons["shipping-delivery"] and "no text" in reasons["about-us"]
    assert "home-page" not in client.fetched and "returns" not in client.fetched      # no request for pages that cannot be used


def test_a_named_page_type_includes_a_page_that_would_otherwise_be_left_out():
    client = PageClient([page("nectar", "<h2>Nectar</h2><p>A mattress brand we carry.</p>")])
    rows = ai.page_rows(client, overrides={"nectar": "brand_info"})
    assert rows and {r["source_type"] for r in rows} == {"brand_info"}


def test_another_stores_demo_data_left_in_a_page_is_flagged():
    other = {"name": "Acme Pets", "phone": "(555) 010-2000", "email": "hello@acmepets.com"}
    warnings = []
    ai.page_rows(PageClient([page("mattress-buying-guide", PAGE_HTML)], store=other), warnings=warnings)
    assert len(warnings) == 1 and warnings[0].startswith("mattress-buying-guide: still has demo data")
    assert "(731) 423-6565" in warnings[0]


def test_a_page_without_the_template_comment_is_never_flagged():
    other = {"name": "Acme Pets", "phone": "(555) 010-2000", "email": "hello@acmepets.com"}
    warnings = []
    ai.page_rows(PageClient([page("about-us", "<h2>About</h2><p>Call (731) 423-6565.</p>")], store=other), warnings=warnings)
    assert warnings == []


def test_ingest_runs_faqs_and_pages_together_and_returns_rows_sync_can_use():
    class Both(PageClient):
        def faqs(self):
            return FAQS
    rows = ai.ingest(Both([page("returns", "<h2>Returns</h2><p>Thirty days.</p>")]), ("faq", "page"))
    assert {r["source_type"] for r in rows} == {"faq", "policy"}
    new, gone, _ = sync.plan_changes(sync.rows_for_types(rows, ["policy", "guide"]), set())
    assert [r["source_key"] for r in new] == ["returns"] and gone == []


def test_the_command_line_refuses_a_bad_page_type_before_doing_anything():
    for bad in ("nectar", "nectar=color", "=policy"):
        with pytest.raises(SystemExit):
            ai.main(["store-a", "--page-type", bad])


def test_strip_boilerplate_removes_any_stores_trailing_contact_footer():
    from rag.text_utils import strip_boilerplate
    body = "Returns are accepted within 30 days.\nCall (555) 123-4567\n\nhelp@acmevet.com"
    assert strip_boilerplate(body) == "Returns are accepted within 30 days."


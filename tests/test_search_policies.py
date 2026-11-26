from bot.context import ChatContext
from bot.tools.search_policies import fuse, keyword_query, search_policies


CTX = ChatContext(store_uuid="s-1")


def row(i, sim, key="returns", st="policy"):
    return {"id": i, "source_type": st, "source_key": key, "title": key.title(), "heading": f"{key} > h{i}",
            "url_path": f"/{key}", "content": f"text {i}", "similarity": sim}


def fake_embed(q):
    return [0.1, 0.2, 0.3]


def runner(vector_rows, keyword_rows, seen=None):
    def run(sql, params):
        if seen is not None:
            seen.append((sql, params))
        return vector_rows if "ORDER BY embedding" in sql else keyword_rows
    return run


def test_keyword_query_drops_stopwords():
    assert keyword_query("What is your return policy?") == "return or policy"
    assert keyword_query("Can I cancel my order?") == "cancel or order"
    assert keyword_query("?!") == ""


def test_found_with_citation_fields():
    out = search_policies({"question": "return policy"}, ctx=CTX, run_query=runner([row(1, 0.8), row(2, 0.6)], []), embed_query=fake_embed)
    assert out["found"] is True
    assert out["chunks"][0]["page"] == "/returns" and out["chunks"][0]["title"] == "Returns"
    assert "Answer ONLY" in out["note"]


def test_below_cutoff_is_not_found_and_reports_best():
    out = search_policies({"question": "coupon codes"}, ctx=CTX, run_query=runner([row(1, 0.41), row(2, 0.3)], [row(3, 0.2)]), embed_query=fake_embed)
    assert out["found"] is False and out["best_similarity"] == 0.41
    assert "Do NOT answer" in out["note"]


def test_keyword_hit_under_cutoff_is_still_dropped():
    out = search_policies({"question": "x"}, ctx=CTX, run_query=runner([row(1, 0.9)], [row(7, 0.2)]), embed_query=fake_embed)
    assert [c["content"] for c in out["chunks"]] == ["text 1"]


def test_fusion_boosts_chunk_found_by_both():
    vec = [row(1, 0.80), row(2, 0.78)]
    kw = [row(2, 0.78)]
    assert [r["id"] for r in fuse(vec, kw, 0.55, 6)] == [2, 1]


def test_top_k_and_default_source_types_are_passed():
    seen = []
    search_policies({"question": "warranty"}, ctx=CTX, run_query=runner([row(i, 0.9) for i in range(8)], [], seen), embed_query=fake_embed)
    assert seen[0][1][2] == ["policy", "guide", "faq"] and seen[0][1][4] == 8


def test_bad_args():
    assert "error" in search_policies({}, ctx=CTX, embed_query=fake_embed)
    assert "error" in search_policies({"question": "x", "sql": "drop"}, ctx=CTX, embed_query=fake_embed)
    assert "error" in search_policies({"question": "x", "source_types": ["product"]}, ctx=CTX, embed_query=fake_embed)


def test_db_failure_returns_safe_error():
    def boom(sql, params): raise RuntimeError("connection refused host=secret")
    out = search_policies({"question": "returns"}, ctx=CTX, run_query=boom, embed_query=fake_embed)
    assert out["error"] == "policy search is unavailable right now" and "secret" not in str(out)


def test_other_showroom_sentences_are_removed_before_the_model_sees_them():
    from bot.tools.search_policies import scrub_text
    text = "Snap Finance welcomes all credit types. Visit our showroom in Brooklyn or Jamaica, Queens for help. Terms are set by the lender."
    out = scrub_text(text)
    assert "Brooklyn" not in out and "Jamaica" not in out
    assert "Snap Finance welcomes all credit types." in out and "Terms are set by the lender." in out
    assert scrub_text("Nothing to remove here.") == "Nothing to remove here."

# ---------- the shared database connection and the start-up warm-up ----------

class _FakeCon:
    def __init__(self, log, fail_first=False):
        self.log, self.closed, self.fail_first = log, False, fail_first

    def execute(self, sql, params):
        import psycopg
        if self.fail_first:
            self.fail_first = False
            raise psycopg.OperationalError("server closed the connection")
        self.log.append(sql)
        return self

    def fetchall(self):
        return [{"ok": 1}]

    def close(self):
        self.closed = True


def _fake_connect(monkeypatch, cons):
    import psycopg
    import bot.tools.search_policies as sp
    opened = []

    def connect(url, **kw):
        con = cons[len(opened)]
        opened.append(url)
        return con
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(sp, "_CONN", None)
    monkeypatch.setattr(sp, "_CONN_URL", None)
    monkeypatch.setenv("RAG_DB_URL", "postgresql://example/db")
    return sp, opened


def test_queries_share_one_open_connection(monkeypatch):
    log = []
    sp, opened = _fake_connect(monkeypatch, [_FakeCon(log)])
    sp._default_run_query("q1", [])
    sp._default_run_query("q2", [])
    assert opened == ["postgresql://example/db"] and log == ["q1", "q2"]      # connected once, used twice


def test_a_dropped_connection_is_replaced_once_and_the_query_retried(monkeypatch):
    log = []
    first, second = _FakeCon(log, fail_first=True), _FakeCon(log)
    sp, opened = _fake_connect(monkeypatch, [first, second])
    assert sp._default_run_query("q", []) == [{"ok": 1}]
    assert len(opened) == 2 and first.closed and log == ["q"]


def test_warm_up_loads_the_model_and_the_connection_and_never_raises(monkeypatch):
    import bot.embeddings as emb
    log, embedded = [], []
    sp, opened = _fake_connect(monkeypatch, [_FakeCon(log)])
    monkeypatch.setattr(emb, "embed_query", lambda q: embedded.append(q) or [0.0])
    sp.warm_up()
    assert embedded and opened and log
    monkeypatch.setattr(emb, "embed_query", lambda q: (_ for _ in ()).throw(RuntimeError("no model")))
    sp.warm_up()                                                               # a failure is logged, never raised


def test_the_service_preloads_only_outside_tests_and_when_allowed(monkeypatch):
    from bot.server import _should_preload
    assert _should_preload(None) is False                                      # pytest is running
    assert _should_preload(True) is True and _should_preload(False) is False

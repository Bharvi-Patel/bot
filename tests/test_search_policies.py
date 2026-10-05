from sjbot.tools.search_policies import fuse, keyword_query, search_policies


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
    out = search_policies({"question": "return policy"}, run_query=runner([row(1, 0.8), row(2, 0.6)], []), embed_query=fake_embed)
    assert out["found"] is True
    assert out["chunks"][0]["page"] == "/returns" and out["chunks"][0]["title"] == "Returns"
    assert "Answer ONLY" in out["note"]


def test_below_cutoff_is_not_found_and_reports_best():
    out = search_policies({"question": "coupon codes"}, run_query=runner([row(1, 0.41), row(2, 0.3)], [row(3, 0.2)]), embed_query=fake_embed)
    assert out["found"] is False and out["best_similarity"] == 0.41
    assert "Do NOT answer" in out["note"]


def test_keyword_hit_under_cutoff_is_still_dropped():
    out = search_policies({"question": "x"}, run_query=runner([row(1, 0.9)], [row(7, 0.2)]), embed_query=fake_embed)
    assert [c["content"] for c in out["chunks"]] == ["text 1"]


def test_fusion_boosts_chunk_found_by_both():
    vec = [row(1, 0.80), row(2, 0.78)]
    kw = [row(2, 0.78)]
    assert [r["id"] for r in fuse(vec, kw, 0.55, 6)] == [2, 1]


def test_top_k_and_default_source_types_are_passed():
    seen = []
    search_policies({"question": "warranty"}, run_query=runner([row(i, 0.9) for i in range(8)], [], seen), embed_query=fake_embed)
    assert seen[0][1][1] == ["policy", "guide", "faq"] and seen[0][1][3] == 8


def test_bad_args():
    assert "error" in search_policies({}, embed_query=fake_embed)
    assert "error" in search_policies({"question": "x", "sql": "drop"}, embed_query=fake_embed)
    assert "error" in search_policies({"question": "x", "source_types": ["product"]}, embed_query=fake_embed)


def test_db_failure_returns_safe_error():
    def boom(sql, params): raise RuntimeError("connection refused host=secret")
    out = search_policies({"question": "returns"}, run_query=boom, embed_query=fake_embed)
    assert out["error"] == "policy search is unavailable right now" and "secret" not in str(out)

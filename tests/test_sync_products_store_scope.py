"""rag/sync_products.py works on ONE store's rows: every statement carries the store, a second store's rows are never read,
changed or deleted, and new rows are inserted with their store (the column is NOT NULL)."""
import json

import pytest

import rag.sync_products as sp

A, B = "store-a", "store-b"


class FakeCon:
    """A tiny stand-in for the rag_chunks table: rows are (store, source_key) -> dict."""

    def __init__(self, table):
        self.table = table
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        sql1 = " ".join(sql.split())
        if sql1.startswith("SELECT source_key, content_hash"):
            return [(k, r["content_hash"]) for (st, k), r in self.table.items() if st == params[0]]
        if sql1.startswith("SELECT source_key FROM rag_chunks"):
            st, keys, roots = params
            return [(k,) for (s2, k), r in self.table.items() if s2 == st and k in keys and set(r["category_slugs"]) & set(roots)]
        if sql1.startswith("DELETE"):
            st, key = params
            self.table.pop((st, key), None)
            return []
        if sql1.startswith("INSERT"):
            st, _type, key = params[0], params[1], params[2]
            self.table[(st, key)] = {"content_hash": params[10], "category_slugs": params[7], "price": params[8]}
            return []
        if sql1.startswith("UPDATE"):
            price, _brand, cats, _title, _url, st, key = params
            if (st, key) in self.table:
                self.table[(st, key)].update(price=price, category_slugs=cats)
            return []
        raise AssertionError(f"unexpected statement: {sql1}")


def product(key, h, price=100.0, cats=("beds",)):
    return {"source_type": "product", "source_key": key, "title": key, "heading": None, "url_path": f"/product/{key}",
            "brand_slug": "acme", "category_slugs": list(cats), "price": price, "content": f"text {key}", "content_hash": h}


@pytest.fixture
def run(tmp_path, monkeypatch):
    def go(rows, table, store=A, roots=("beds",)):
        (tmp_path / "products.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf8")
        (tmp_path / "products_scope.json").write_text(json.dumps({"roots": list(roots)}), encoding="utf8")
        monkeypatch.chdir(tmp_path)
        con = FakeCon(table)
        monkeypatch.setattr(sp.psycopg, "connect", lambda url: con)
        monkeypatch.setenv("RAG_DB_URL", "postgresql://fake")
        monkeypatch.setattr(sp, "embed_documents", lambda texts: [[0.0] * 3 for _ in texts])
        sp.main(["sync_products.py", store])
        return con
    return go


def row(h, cats=("beds",)):
    return {"content_hash": h, "category_slugs": list(cats), "price": 1.0}


def test_every_statement_names_the_store(run):
    table = {(A, "p1"): row("old"), (A, "p2"): row("same"), (A, "gone"): row("x")}
    con = run([product("p1", "new"), product("p2", "same"), product("p3", "h3")], table)
    assert con.statements
    for sql, params in con.statements:
        assert "store_uuid" in sql, sql
        assert A in params, (sql, params)


def test_the_other_stores_rows_are_never_read_changed_or_deleted(run):
    table = {(A, "p1"): row("old"), (B, "p1"): row("b-hash"), (B, "only-b"): row("b2"), (A, "gone"): row("x")}
    before_b = {k: dict(v) for k, v in table.items() if k[0] == B}
    con = run([product("p1", "new"), product("p9", "h9")], table)
    assert {k: v for k, v in table.items() if k[0] == B} == before_b          # store B untouched
    assert all(B not in params for _, params in con.statements)
    assert (A, "gone") not in table and (A, "p9") in table and table[(A, "p1")]["content_hash"] == "new"


def test_new_rows_are_inserted_with_their_store(run):
    table = {}
    run([product("p1", "h1")], table, store=B)
    assert list(table) == [(B, "p1")]


def test_removal_stays_inside_the_built_categories(run):
    table = {(A, "kept-other-category"): row("x", cats=("rugs",)), (A, "gone-in-scope"): row("y", cats=("beds",))}
    run([product("p1", "h1")], table)
    assert (A, "kept-other-category") in table and (A, "gone-in-scope") not in table


@pytest.mark.parametrize("argv", [["sync_products.py"], ["sync_products.py", "bad id"], ["sync_products.py", "a", "b"], ["sync_products.py", ""]])
def test_a_valid_store_id_is_required(argv):
    with pytest.raises(SystemExit):
        sp.main(argv)

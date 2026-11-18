"""One-time migration for an existing rag_chunks table: add the store_uuid column.

    python rag/migrate_add_store_uuid.py <store uuid that owns the rows already in the table>

Every existing row is marked as belonging to that store (the South Jackson store, today). Safe to run twice.
After it, search_policies only ever reads the chat's own store's rows.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from bot.store_config import valid_store_id


def statements(store_uuid: str) -> list[tuple[str, tuple]]:
    if not valid_store_id(store_uuid):
        raise ValueError("not a valid store id")
    return [
        ("ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS store_uuid text", ()),
        ("UPDATE rag_chunks SET store_uuid = %s WHERE store_uuid IS NULL", (store_uuid,)),
        ("ALTER TABLE rag_chunks ALTER COLUMN store_uuid SET NOT NULL", ()),
        ("CREATE INDEX IF NOT EXISTS rag_chunks_store_type ON rag_chunks (store_uuid, source_type)", ()),
    ]


def main() -> None:
    import psycopg
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with psycopg.connect(os.environ["RAG_DB_URL"]) as con:
        for sql, params in statements(sys.argv[1]):
            con.execute(sql, params)
        n = con.execute("SELECT count(*) FROM rag_chunks WHERE store_uuid = %s", (sys.argv[1],)).fetchone()[0]
        ver = con.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    print(f"done: {n} rows now belong to store {sys.argv[1]}; pgvector version {ver[0] if ver else 'unknown'}")
    print("Check: with a store filter, the HNSW index can return fewer rows than asked for. For this small a table an exact scan "
          "is fine; if results look short for a small store, see the notes in the project doc.")


if __name__ == "__main__":
    main()

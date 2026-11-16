"""One place for the embedding model, so the nightly sync (rag/sync.py) and the search tool can never drift apart.
Nomic Embed v1.5 (768 dims = vector(768) in rag/schema.sql). Nomic needs these prefixes, so do not remove them.
Changing MODEL means re-embedding every chunk."""
from __future__ import annotations

from functools import lru_cache

MODEL = "nomic-ai/nomic-embed-text-v1.5"


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer   # loaded once per process (~0.5 GB, a few seconds)
    return SentenceTransformer(MODEL, trust_remote_code=True)


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _model().encode(["search_document: " + t for t in texts], normalize_embeddings=True).tolist()


def embed_query(question: str) -> list[float]:
    return _model().encode("search_query: " + question, normalize_embeddings=True).tolist()

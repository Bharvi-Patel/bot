-- Postgres + pgvector, exactly as in section 5.3 of the data map
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE rag_chunks (
  id            bigserial PRIMARY KEY,
  source_type   text NOT NULL,      -- policy | guide | brand_info | blog | faq | product
  source_key    text NOT NULL,      -- page slug, faq uuid, or product_uuid
  title         text,
  heading       text,
  url_path      text,               -- where the customer can read it on the site
  brand_slug    text,               -- product docs only
  category_slugs text[],            -- product docs only
  price         numeric(10,2),      -- product docs only (filter hint, never quoted)
  content       text NOT NULL,
  content_hash  text NOT NULL,      -- sha256(content); re-embed only when it changes
  embedding     vector(768),        -- match your embedding model's dimension
  updated_at    timestamptz DEFAULT now()
);
CREATE INDEX ON rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON rag_chunks (source_type);

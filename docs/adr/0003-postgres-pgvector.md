# ADR-0003: PostgreSQL + pgvector as the single system of record (SQLite only for tests/quickstart)

- Status: Accepted

## Context
RAG needs vector similarity, keyword search and metadata filtering with tenant isolation, alongside relational data.

## Decision
Use PostgreSQL 16 with pgvector (HNSW, cosine) and built-in full-text search (GIN expression index), managed by
Alembic migrations. The `VectorStore` adapter has a `PgVectorStore` and an `InMemoryScanStore` (exact scan + BM25)
used for SQLite. SQLite is used (a) by the default test suite for speed and portability and (b) by the explicit
"quickstart" mode when Docker is unavailable; the same suite runs on PostgreSQL via `make test-pg`.

## Consequences
+ One datastore for transactional + vector + text data; tenant filters are ordinary WHERE clauses.
+ Vector writes are transactional with documents/chunks (no dual-write consistency problem).
− At very large scale (≫10M chunks) a dedicated vector DB may be needed; the adapter boundary allows it.
− SQLite mode creates schema from metadata (not migrations); it is never used for staging/production.

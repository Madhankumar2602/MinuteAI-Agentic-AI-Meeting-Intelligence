# ADR 0002 — pgvector instead of a dedicated vector database

- **Status:** Accepted
- **Date:** 2026-09-12
- **Milestone:** M1 (extension enabled) → M6 (first use)

## Context

M6 and M7 require semantic search over meeting transcript chunks: embed the
user's question, find the nearest chunks, feed them to the LLM as grounding
context.

The critical constraint is **access control**. A user must never retrieve
another user's meeting content — including indirectly, through a RAG answer
that quotes it. Every similarity search is therefore a filtered search.

## Decision

Store embeddings in PostgreSQL using the `pgvector` extension, as a
`vector(384)` column on `meeting_chunks`. No separate vector database.

The extension is enabled in migration `0001` during M1, even though nothing
stores a vector until M6, so that the dependency is proven on day one rather
than discovered to be unavailable in week nine.

## Alternatives considered

**Pinecone / Weaviate / Qdrant / Chroma.** Purpose-built, with better
performance at very large scale. Rejected for three reasons:

1. **ACL filtering becomes a distributed-systems problem.** Permissions live in
   PostgreSQL. With an external vector store, every search either duplicates
   ownership metadata into the vector index (which must then be kept in sync on
   every share, transfer, and delete) or over-fetches and filters in Python
   (which silently degrades recall — filtering 10 results down to 2 is not the
   same as retrieving the top 10 the user may actually see).
2. **No transactional consistency.** Deleting a meeting would delete its rows
   from PostgreSQL and *separately* need to delete its vectors. Any failure in
   between leaves orphaned vectors that can still surface in search results —
   a data leak, not merely a bug.
3. **Scale does not justify it.** The realistic corpus is a few hundred
   meetings, on the order of 10⁴–10⁵ chunks. pgvector's HNSW index handles this
   comfortably; the crossover where a dedicated store wins is orders of
   magnitude further out.

**FAISS in-process.** No persistence, no concurrency story, and the index would
need rebuilding on every restart. Suitable for a notebook, not a service.

## Decision detail

- **Dimension 384**, from `sentence-transformers/all-MiniLM-L6-v2`. Fast on
  CPU, and small enough that index size stays negligible.
- **Cosine distance** (`vector_cosine_ops`), the standard metric for normalised
  sentence embeddings.
- **HNSW index** rather than IVFFlat: no training step, no requirement to
  rebuild after bulk inserts, and better recall at small corpus sizes. IVFFlat's
  advantage (faster build on very large datasets) is irrelevant here.
- The embedding column lives **on `meeting_chunks`**, not in a separate
  `embeddings` table. The relationship is strictly 1:1, so a separate table
  would force a join on the hottest query in the system for no benefit.

## Consequences

**Advantages**
- Authorization is a `JOIN` and a `WHERE` clause — the same mechanism that
  protects every other query, reviewable in one place.
- Deleting a meeting removes its vectors in the same transaction, by foreign
  key cascade. Orphaned vectors are structurally impossible.
- One database to back up, monitor, and deploy.

**Limitations**
- Vector search competes with OLTP queries for the same connection pool and CPU.
  At this scale that is acceptable; at much larger scale a read replica would be
  the first mitigation.
- `pgvector` must be available on the deployment target. Verified locally on
  `pgvector/pgvector:pg16` (extension version 0.8.6); **must be re-verified on
  RDS before M10 depends on it** — this is tracked as a known risk.

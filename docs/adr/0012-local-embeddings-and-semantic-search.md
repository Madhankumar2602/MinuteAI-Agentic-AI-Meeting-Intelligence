# ADR 0012 — Local embeddings with ONNX Runtime, turn-based chunking, filtered HNSW search

- **Status:** Accepted
- **Date:** 2026-09-14
- **Milestone:** M6
- **Builds on:** ADR 0002 (pgvector, `vector(384)`, cosine, HNSW)

## Context

M6 makes transcripts searchable by meaning and provides the retrieval step M7's
RAG answers will use. ADR 0002 had already chosen the store (pgvector) and the
model (`sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions). Four questions
were still open:

1. How to run the model.
2. How to cut transcripts into passages.
3. Where indexing runs, and how stored vectors are kept in step with transcript edits.
4. How to keep filtered vector search correct: a user must get their own nearest
   passages, not fewer results because other users' passages were closer.

## Decisions

### 1. Run the official ONNX export with ONNX Runtime, not PyTorch

The model repository publishes an ONNX export (`onnx/model.onnx`). MinuteAI loads
it with `onnxruntime` and the model's own `tokenizer.json` with `tokenizers`, then
applies the same steps sentence-transformers does. Those steps are read from the
model's `modules.json`: mean pooling over the attention mask, then L2
normalisation, with truncation at 256 tokens.

| | sentence-transformers + PyTorch | ONNX Runtime (chosen) |
|---|---|---|
| Install size | ~1 GB (torch) | ~60 MB |
| Container / Lambda (M10–M11) | Heavy image, slow cold start | Small |
| Output | Reference | **Identical within 6.4 × 10⁻⁷** (tested) |
| Code we own | None | ~60 lines of pooling and batching |

The parity claim is tested, not assumed. `tests/fixtures/embeddings/minilm_reference.json`
holds vectors produced by sentence-transformers 6.0.1 on torch 2.14.0, generated
once in a separate throwaway environment. `test_embedding_model.py` checks the
ONNX vectors against them to within 1e-4. The texts cover a normal sentence, a
single word, non-ASCII text, and an input over 256 tokens, so truncation must
match too. Mutation checks confirmed the test fails if pooling is switched to the
CLS token, if truncation is set to 128 (the value in `tokenizer.json`, which
sentence-transformers overrides), or if normalisation is dropped.

- **Pinned revision.** The model is fetched by commit hash (`EMBEDDING_MODEL_REVISION`)
  and cached in `~/.cache/huggingface`. Vectors are only comparable when they come
  from the same weights, so an upstream change can never silently mix old and new
  vectors.
- **Local, not an API.** Transcripts never leave the machine to be embedded: no
  quota, no per-token cost, and it works offline once cached. Gemini embeddings
  were not chosen because they would add a second external data flow and a quota
  shared with extraction.

### 2. Chunk by speaker turn, at most 160 tokens, 32 tokens of overlap

- **Units are speaker turns (lines).** A turn is the natural unit of meaning in a
  meeting. A turn longer than the budget is split at sentence ends, then at words,
  and finally, for an unbroken string, at fixed character widths.
- **Turns are packed greedily** into chunks of at most 160 model tokens. Each
  chunk starts with the tail of the previous one (up to 32 tokens) so an exchange
  that straddles a boundary can still be retrieved whole.
- **Every chunk is an exact slice** of the transcript (`char_start`/`char_end`), so
  a result can open the transcript at the right place, and M7 can cite precisely.
- **Counts come from the model's tokenizer**, not a words-per-token estimate. Piece
  counts are added rather than recounted; this is exact because WordPiece splits
  on whitespace first, and a test verifies it with the real tokenizer.
- **Why 160.** The model reads 256 tokens but was trained on passages of up to 128.
  160 stays well inside the limit while carrying a few turns of context.
  `python -m app.evaluation.retrieval` (18 paraphrased questions, 2 meetings)
  gives hit@1 0.94 and MRR 0.96 at 160, against a chance hit@1 of 0.19. This set
  is too small to rank chunk sizes: with only a handful of chunks, larger chunks
  win by default. It shows retrieval works; M12 evaluates it properly.

The chunker is versioned (`CHUNKER_VERSION = "turns-v1"`). Changing the
algorithm or its parameters means bumping it.

### 3. Index inside the processing job, before extraction; provenance on every chunk

```
[transcribe] ─► index (chunk + embed + replace) ─► extract
```

- **Before extraction.** Indexing is local and deterministic, so a meeting is
  searchable even when the LLM is rate-limited or down.
- **Idempotent.** Each chunk stores `transcript_sha256`, `embedding_model`
  (name@revision) and `chunker_version`. If all three match, indexing is a no-op.
  A retry after an LLM failure does not embed again (tested: one embedding call
  across two attempts).
- **Stale chunks can never be returned.** Search joins `transcripts` on
  `content_sha256 = transcript_sha256`, so editing a transcript hides its old
  chunks immediately, before re-processing, with no second write path to
  maintain. Reprocessing replaces them in one transaction.
- **Existing meetings are picked up automatically.** `POST /process` only answers
  "cached" when the extraction *and* the index are current. A meeting processed
  before M6 gets a job that indexes it without calling the LLM again (tested).
- **Transient failures are retried.** `EmbeddingUnavailableError` (for example, the
  model download failing) is retryable like LLM rate limits, and extraction waits
  for it.

### 4. Access control and ranking in one SQL statement, with iterative HNSW scans

```sql
SELECT … FROM meeting_chunks c
JOIN meetings m    ON m.id = c.meeting_id AND m.owner_id = :user
JOIN transcripts t ON t.meeting_id = c.meeting_id AND t.content_sha256 = c.transcript_sha256
WHERE c.embedding_model = :model AND c.chunker_version = :version
ORDER BY c.embedding <=> :query LIMIT :k
```

An HNSW index returns its `ef_search` nearest candidates, and PostgreSQL filters
them afterwards. If those candidates belong to other users, the user can get
fewer results than they should, possibly none. This is exactly the recall problem
ADR 0002 rejected external vector stores over, and it exists inside PostgreSQL
too.

pgvector 0.8 adds **iterative index scans**. When filtering leaves too few rows,
the scan continues into the index. Search runs
`SET LOCAL hnsw.iterative_scan = strict_order` (and `ef_search = 100`) in its
transaction. `SET LOCAL` means the setting cannot leak to other requests on the
pooled connection.

**Tested with evidence.** Another user has 300 chunks right next to the query, and
the user has 3 further away. The planner is steered onto the HNSW index, and
`EXPLAIN` asserts it is used. Search returns all 3. The control run of the same
statement with iterative scanning off returns **0**. Removing the `SET LOCAL`
makes the test fail.

At current data sizes PostgreSQL usually filters by owner first and sorts exactly
(checked with `EXPLAIN`). The index path, and so this protection, matters once a
user's history is large.

## Consequences

- One more runtime dependency set (`onnxruntime`, `tokenizers`, `huggingface-hub`,
  `numpy`, `pgvector`). About 90 MB of model files are downloaded on first use.
  M10 should bake them into the image rather than fetch them at cold start.
- Embedding uses API-process CPU when the worker is embedded: about 1 s to load,
  then roughly 25 ms per query and seconds for a long meeting. The standalone
  worker (ADR 0008) moves indexing off the API if needed.
- Scores are cosine similarities, not percentages. The UI labels them in plain
  language (Strong / Good / Weak), from bands observed in live checks. Nothing is
  filtered by score.
- A chunk of several turns scores lower than the single line that answers a
  question, because the other turns dilute it. M7 may need line-level
  re-scoring inside the top chunks for precise citations.
- Changing the model or chunker makes every stored chunk stale. Search ignores
  stale chunks, and re-processing rebuilds them. A bulk re-index command is not
  built yet.

# MinuteAI — Architecture

> Living document. Updated as each milestone lands.
> Current state: **M4 complete**. Sections marked *(planned)* are not built yet.

## 1. System overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  React SPA  (planned, M5)                                                │
│  Login · Dashboard · Meeting detail · Ask (RAG) · Agent alerts           │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ HTTPS + JWT Bearer          (POST /process → 202, poll job)
┌───────────────▼──────────────────────────────────────────────────────────┐
│  FastAPI process                                  [M1–M4: BUILT]         │
│                                                                          │
│  middleware  RequestIDMiddleware · CORS                                  │
│  api/v1      auth · meetings · intelligence · action-items · jobs · media│
│  services    authorization · intelligence · grounding · prompts          │
│              job_store · dynamo · storage · media_validation ·           │
│              transcription · llm/ (base · gemini · factory)             │
│  workers     ProcessingWorker — embedded by default, or standalone:      │
│              python -m app.workers.processing                            │
└───┬──────────────────────────┬───────────────────────────────┬───────────┘
    │ SQLAlchemy (async)       │ boto3 (thread pool)            │ HTTPS
┌───▼───────────────────┐  ┌───▼──────────────────────────┐  ┌──▼───────────┐
│ PostgreSQL 16         │  │ DynamoDB (Local in dev)      │  │ Google Gemini│
│ + pgvector 0.8.6      │  │                              │  │ gemini-3.6-  │
│ SOURCE OF TRUTH       │  │ minuteai_processing_jobs     │  │ flash        │
│ users, meetings,      │  │   job items  (TTL 30 days)   │  └──────────────┘
│ transcripts,          │  │   meeting lock items         │
│ summaries, decisions, │  │   gsi_meeting · gsi_status   │  ┌──────────────┐
│ action_items,         │  │ (+ agent runs — planned M8)  │  │ S3 (RustFS   │
│ meeting_participants, │  └──────────────────────────────┘  │ locally)     │
│ meeting_media         │                                    │ recordings,  │
│ (+ chunks — planned)  │   browser ──presigned POST────────►│ raw          │
└───────────────────────┘                                    │ transcripts  │
                                                             └──────────────┘
         EventBridge → Lambda → Agent   (planned, M11)
```

## 2. Component responsibilities

| Component | Responsibility | Does **not** do |
|---|---|---|
| `app/main.py` | App factory; lifespan ensures the jobs table, starts and drains the embedded worker | Business logic |
| `app/core/config.py` | Typed settings from repo-root `.env`; API key as `SecretStr` | Read environment ad hoc elsewhere |
| `app/core/logging.py` | JSON/console formatters, request-id context, secret redaction | Decide log levels per route |
| `app/core/middleware.py` | Assign/propagate `X-Request-ID`, log every request outcome | Authentication |
| `app/core/security.py` | Argon2id hashing, JWT issue/verify | Database access |
| `app/core/exceptions.py` | Domain error types → one JSON error envelope | Raise errors itself |
| `app/services/authorization.py` | **The** meeting access rule; item-level checks delegate to it | HTTP concerns |
| `app/services/llm/` | Provider-neutral contract; Gemini implementation with retries and validation | Business logic |
| `app/services/prompts.py` | Versioned system instruction and prompt builder | Call the model |
| `app/services/intelligence.py` | Extraction pipeline: cache check, LLM call, normalisation, atomic persistence | Queueing, retries, HTTP |
| `app/services/grounding.py` | Verify evidence quotes exist in the transcript | Decide what to keep |
| `app/services/job_store.py` | DynamoDB job queue: submit with lock, claim, lease, requeue, finish, recover | Business data |
| `app/services/storage.py` | S3: presigned POST with policy, head, ranged read, download, prefix delete | Decide what is valid |
| `app/services/media_validation.py` | MIME allow-list, file-signature sniffing, filename sanitising | I/O |
| `app/services/transcription.py` | When to transcribe (etag rule); download → transcribe → archive raw → store transcript | Queueing |
| `app/workers/processing.py` | Run jobs: claim → heartbeat → [transcribe] → extract → classify failure → transition | HTTP |
| `app/api/v1/` | HTTP contract, status codes, Pydantic validation | Direct SQL against a meeting by id |

## 3. Request lifecycle

```
HTTP request
  → RequestIDMiddleware      assign/propagate X-Request-ID, start timer
  → CORSMiddleware           origin check
  → route                    Pydantic validates the body
  → get_current_user         decode JWT → load User → check is_active
  → authorize_*_access       (resource routes) → 404 if not permitted
  → handler / service        business logic, explicit commit
  → response_model           serialise (password_hash structurally absent)
  → RequestIDMiddleware      log method/path/status/duration, set response header
```

Any `AppError` raised anywhere, including LLM and job-store errors, becomes:

```json
{"error": {"code": "...", "message": "...", "request_id": "..."}}
```

## 4. Asynchronous processing (M3 — ADR 0008)

```
PUT  /meetings/{id}/transcript
POST /meetings/{id}/process                                  median 83 ms
  │  authorize (WRITE) → transcript exists? no → 409 transcript_missing
  │  results current and not force and no active job? → 200 {cached: true, job: null}
  │  DynamoDB TransactWrite: put JOB (QUEUED) + put LOCK#MEETING  IF NOT EXISTS
  │      lock exists → return the active job (no duplicate run)
  │  meeting.status = queued ; worker.notify()
  └► 202 {job: {job_id, status: QUEUED, events: [queued]}}

GET /jobs/{job_id}   ← client polls; strongly consistent GetItem

Worker loop (every 2 s, or immediately on notify)
  recover_stale_jobs: PROCESSING with lease expired → requeue / fail
  claim_next: oldest QUEUED with available_at ≤ now
              UpdateItem IF job_status=QUEUED AND available_at=<seen>   → exactly one winner
  ├─ heartbeat: renew lease every lease/3 IF worker_id = me
  ├─ meeting deleted / owner changed → FAILED meeting_not_found
  ├─ results already current (recovered after a late crash) → COMPLETED cached
  └─ run_extraction (§5)
       ├─ ok                     → TransactWrite: job COMPLETED + delete lock   → meeting completed
       ├─ transient, attempts left → job QUEUED, retry at 30 s × 4^(n−1)        → meeting queued
       └─ permanent / exhausted  → TransactWrite: job FAILED + delete lock      → meeting failed
  All outcome writes are conditional on worker_id = me (fencing).
```

**Error classification.** Retried: `LLMRateLimitError`, `LLMUnavailableError`,
`JobStoreUnavailableError`, database `OperationalError`. Failed immediately:
everything else, including a bad key, invalid model output after a re-roll,
a missing transcript, a deleted meeting, and unexpected exceptions. Retrying
those would spend quota to reproduce the same error. Job error messages come
only from `AppError` classes. Raw exception text, which can contain SQL or
internals, stays in the logs.

**Verified live** (not just in tests):
- A worker was hard-killed mid-Gemini call. A fresh worker recovered the job
  20.5 s later (lease 20 s) and completed it on attempt 2 with correct results.
- A job left over from an earlier killed worker was recovered and completed the
  same way.
- The API and the worker ran as separate processes, and separately in embedded
  mode.
- Three jobs submitted together ran at most two at a time
  (`WORKER_CONCURRENCY=2`).

## 5. Recordings (M4 — ADR 0009)

```
POST /meetings/{id}/media/upload-url   {filename, content_type, size_bytes}
  │  authorize (WRITE) · allow-listed type · size ≤ MEDIA_MAX_BYTES
  │  key = users/{user}/meetings/{meeting}/source/{uuid}.{ext}     (never the filename)
  └► {upload_url, fields (POST policy: key · Content-Type · content-length-range), upload_token}

browser ── multipart POST ──► S3        storage rejects wrong key / type / size (400)

POST /meetings/{id}/media/complete     {upload_token, replace_manual_transcript?}
  │  token: type=media_upload, same user + meeting, not expired
  │  typed transcript exists and not replacing → 409 manual_transcript_exists
  │  HEAD object (size, type from storage) · ranged GET 64 bytes → signature must match
  │     mismatch → DELETE object → 422 media_invalid
  │  upsert meeting_media (old object deleted after commit) · meeting.source_type = audio|video
  └► 202 {media, job}   (processing auto-queued)

Worker, before extraction:
  media_needing_transcription()  — typed transcript wins; same etag = cached
  download to private temp dir → Gemini Files API → validated segments
  → "Speaker: words" text → raw JSON archived to S3 → transcript row (source=transcription)
  → job events transcription_started / transcription_completed → extraction (§6)
```

**Measured live** (154 s recording, 4.9 MB): upload 0.72 s direct to storage;
transcription 23–31 s; upload-to-results 47 s. Extraction on the transcribed
text was fully correct. Known weaknesses of the transcription itself (see
ADR 0009): two speaker turns misattributed, model timestamps unreliable, name
spelling varies between runs.

## 6. Extraction pipeline (M2 — ADR 0007)

```
run_extraction(meeting_id)
  │  load meeting + transcript
  ├─ meeting.status = PROCESSING ; COMMIT   (releases DB connection during LLM call)
  ├─ LLMProvider.generate_structured(MeetingExtraction)
  │     Gemini, response schema, temperature 0.1
  │     retries 429 / 5xx / network with backoff; one re-roll on invalid JSON
  │     local Pydantic validation (trust boundary)
  ├─ normalise_extraction()   deterministic: trim, dedupe, parse and bound dates,
  │                           verify evidence quotes, add unlisted owners
  ├─ ONE transaction: replace participants, decisions, action items, summary
  │  (with provenance) → status COMPLETED
  └─ on any failure: ROLLBACK and re-raise; the worker decides queued vs failed
```

**Measured** (`gemini-3.6-flash`, 319-word transcript): 13–20 s LLM time,
882 input / ~480 output tokens.

**Prompt-injection defence** is layered. The transcript is fenced and declared
untrusted, output is schema-constrained, and nothing the model returns is
executed. The fixture's injection line was verified not to become an action
item.

## 7. Data model

```
users 1──N meetings 1──1 transcripts
              ├──1 summaries            (provenance: provider, model, prompt_version,
              │                          transcript_sha256, tokens, latency)
              ├──N meeting_participants ──0..1 users
              ├──N decisions
              └──N action_items ──0..1 meeting_participants
```

| Table | Key columns | Notes |
|---|---|---|
| `users` | email (unique), password_hash, is_active | |
| `meetings` | owner_id → users (CASCADE), meeting_date, source_type, status | status: created / **queued** / processing / completed / failed |
| `transcripts` | meeting_id (unique), content, content_sha256, counts, source, media_id, media_etag, raw_s3_key, transcription_model, duration_seconds | One per meeting; provenance set only when transcribed |
| `meeting_media` | meeting_id (unique), s3_key, content_type, size_bytes, etag, original_filename | Row exists only for verified uploads |
| `summaries` | meeting_id (unique), summary_text, key_points (JSONB), provenance | |
| `meeting_participants` | meeting_id, display_name, name_key, user_id (SET NULL) | UNIQUE (meeting_id, name_key) |
| `decisions` | meeting_id, position, decision_text, evidence_*, status | open / resolved / superseded |
| `action_items` | meeting_id, task, owner_name, owner_participant_id, deadline, deadline_text, priority, status, evidence_* | pending / in_progress / done / cancelled |

**Enumerations** are `VARCHAR` with a database `CHECK` constraint, not native
PostgreSQL `ENUM`s (which cannot gain a value inside a transaction block).

> **Correction (M3).** Earlier versions of this document claimed these CHECK
> constraints existed from M1. They did not: SQLAlchemy 2.0 only emits them
> with `create_constraint=True`, and PostgreSQL accepted `status = 'nonsense'`.
> Migration `0003` added all six constraints. `tests/test_schema_constraints.py`
> now inserts invalid values with raw SQL and asserts they are rejected.

**Other choices**: a fixed constraint-naming convention (all three migrations
round-trip cleanly), original wording kept beside resolved values (`owner_name`,
`deadline_text`), and cascade deletes from meetings.

**Indexes worth knowing**: `(owner_id, meeting_date DESC)` for the dashboard;
`(status, deadline)` on action items for overdue queries and the M8 agent.

**DynamoDB** — see §4 and ADR 0008 for the job table design.

**Planned:** `meeting_chunks` with `embedding vector(384)` (M6); `agent_alerts`,
`agent_followups` (M8); `meeting_shares` (ADR 0004).

## 8. Authentication and authorization

**Authentication.** OAuth2 password flow → JWT (HS256, 60 min), re-checked
against the database on every request.

**Authorization.** Ownership only (ADR 0004), one rule reached from every entry
point:

| Resource | Mechanism |
|---|---|
| Meeting and everything under `/meetings/{id}/…` (incl. `/jobs`, `/media`) | `authorize_meeting_access` |
| Upload confirmation | signed upload token must name the same user and meeting, then `authorize_meeting_access` |
| Action item / decision by id | load → delegate to the meeting check |
| Job by id (`/jobs/{id}`) | load from DynamoDB → delegate to the meeting check |
| Cross-meeting lists | `JOIN meetings WHERE owner_id = :me` in the query |
| Worker | re-checks the meeting still exists and belongs to the owner captured at submission |

Every "not permitted" answer is **404**, identical to "does not exist". An
intruder's `POST /process` is rejected before a job is created, so it never
reaches the LLM (tested).

## 9. Security measures in place

| Measure | Where |
|---|---|
| Argon2id hashing, transparent rehash | `core/security.py` — ADR 0005 |
| Login does not reveal registered emails | `api/v1/auth.py` |
| 404-not-403 on foreign resources, including jobs | `services/authorization.py`, `api/v1/jobs.py` |
| Gemini key as `SecretStr`, redacted from logs | `core/config.py`, `core/logging.py` |
| Transcript text never logged | `api/v1/intelligence.py` |
| Transcript size bounded; NUL bytes rejected | `schemas/transcript.py` |
| Explicit `null` on NOT NULL fields → 422 | `schemas/common.py` |
| Database CHECK constraints on every enum column | migration `0003` |
| Job errors expose only safe `AppError` messages | `workers/processing.py` |
| Duplicate submissions cannot trigger duplicate LLM spend | `services/job_store.py` |
| Uploads bypass the API; storage enforces key, type, and size via POST policy | `services/storage.py` |
| File-signature sniffing; disguised files deleted | `services/media_validation.py`, `api/v1/media.py` |
| Upload tokens typed and bound to user + meeting | `core/security.py` |
| User filenames never used in keys or paths | `services/storage.py` |
| Recording deleted from the provider after each transcription | `services/llm/gemini.py` |
| Meeting deletion removes its S3 objects | `api/v1/meetings.py` |
| Prompt-injection defences | `services/prompts.py` |
| API docs disabled in production | `main.py` |

**Known gaps, deliberately deferred:** no rate limiting on login or `/process`;
no refresh tokens; no password reset.

## 10. Configuration

One `.env` at the repository root serves Docker Compose and the backend, typed
and validated at start-up.

| Group | Variables |
|---|---|
| LLM | `GEMINI_API_KEY`, `GEMINI_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `TRANSCRIPT_MAX_CHARS` |
| Jobs | `DYNAMODB_JOBS_TABLE`, `DYNAMODB_AUTO_CREATE_TABLES` (false in AWS) |
| Storage | `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_AUTO_CREATE_BUCKET`, `MEDIA_MAX_BYTES`, `MEDIA_UPLOAD_URL_TTL_SECONDS`, `GEMINI_TRANSCRIPTION_MODEL` |
| Worker | `WORKER_EMBEDDED`, `WORKER_CONCURRENCY`, `WORKER_POLL_SECONDS`, `JOB_LEASE_SECONDS`, `JOB_MAX_ATTEMPTS`, `JOB_RETRY_BASE_SECONDS`, `JOB_TTL_DAYS` |

## 11. Local port allocation

| Port | Service |
|---|---|
| 5432 | PostgreSQL 16 + pgvector |
| 8001 | DynamoDB Local (container port 8000) |
| 9000 | S3-compatible object storage (RustFS) |
| 8010 | FastAPI (8000 is occupied by an unrelated local project) |

## 12. Testing strategy

| Layer | How | Files |
|---|---|---|
| Pure logic | No DB, network, or LLM | `test_normalisation.py`, `test_logging_hygiene.py` |
| LLM provider | SDK stubbed; retry and error mapping | `test_gemini_provider.py` |
| Object storage | **Real local S3 server**: presigned POST policies probed by sending violating uploads; per-session test bucket | `test_media.py` |
| Upload validation | Signature sniffing for 9 containers, disguised files, filename sanitising; mutation-checked | `test_media_validation.py` |
| Job store | **Real DynamoDB Local**: transactions, conditional writes, GSIs; injected clock for leases and back-off | `test_job_store.py` |
| API + worker + DB | Real routes, test PostgreSQL, test DynamoDB table; `FakeLLMProvider`; worker driven explicitly | `test_jobs.py`, `test_processing.py`, `test_action_items.py`, `test_auth.py`, `test_meetings.py`, `test_health.py` |
| Database integrity | Raw SQL bypassing the app | `test_schema_constraints.py` |
| Real provider | Opt-in (`RUN_LIVE_LLM_TESTS=1 pytest -m live`) | `tests/live/` |

- PostgreSQL: dedicated `minuteai_test` database, schema from the real
  migrations, each test rolled back via savepoints.
- DynamoDB: a uniquely named table per session, emptied after every test.
- Concurrency claims (one job per meeting, one winner per claim) are tested with
  genuinely concurrent `asyncio.gather` calls against DynamoDB Local.
- **Logging is exercised for real.** Two bugs were found in M3: Alembic's
  `fileConfig` had been silently disabling every application logger during
  tests, and a reserved `LogRecord` key (`created`) crashed a request in
  production only. Tests now run app loggers at DEBUG, and a static test rejects
  reserved keys in any `extra=`.

## 13. Architecture decision records

| ADR | Decision |
|---|---|
| [0001](adr/0001-polyglot-persistence.md) | PostgreSQL + DynamoDB + S3, each for one job |
| [0002](adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector, not a dedicated vector database |
| [0003](adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](adr/0004-ownership-only-authorization-in-m1.md) | Ownership-only authorization, sharing as extension point |
| [0005](adr/0005-argon2-over-bcrypt.md) | Argon2id via argon2-cffi |
| [0006](adr/0006-gemini-as-initial-llm-provider.md) | Google Gemini as the initial LLM provider |
| [0007](adr/0007-structured-extraction-with-deterministic-validation.md) | Structured LLM extraction with deterministic post-processing |
| [0008](adr/0008-dynamodb-job-queue-with-leased-workers.md) | Background processing on a DynamoDB job queue with leased workers |
| [0009](adr/0009-recording-upload-and-transcription.md) | Presigned-POST uploads, signature validation, RustFS locally, Gemini transcription stage |

Milestone status: [PROJECT_STATUS.md](PROJECT_STATUS.md).

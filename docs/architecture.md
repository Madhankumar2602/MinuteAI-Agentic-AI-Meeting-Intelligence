# MinuteAI — Architecture

> Living document. Updated as each milestone lands.
> Current state: **M2 complete**. Sections marked *(planned)* are not built yet.

## 1. System overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  React SPA  (planned, M5)                                                │
│  Login · Dashboard · Meeting detail · Ask (RAG) · Agent alerts           │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ HTTPS + JWT Bearer
┌───────────────▼──────────────────────────────────────────────────────────┐
│  FastAPI backend                               [M1 + M2: BUILT]          │
│                                                                          │
│  middleware  RequestIDMiddleware · CORS                                  │
│  api/v1      auth · meetings · intelligence · action-items/decisions     │
│              (+ jobs, rag, agent — planned)                              │
│  core        config · logging · security · deps · exceptions             │
│  services    authorization · intelligence · grounding · prompts · dynamo  │
│              llm/ (base · gemini · factory)   (+ rag, agent — planned)   │
│  db          models · session · migrations                               │
└───┬───────────────────────────┬──────────────────────────────┬───────────┘
    │                           │                              │ HTTPS
┌───▼───────────────────┐  ┌────▼─────────────────────┐  ┌─────▼──────────┐
│ PostgreSQL 16         │  │ DynamoDB (Local in dev)  │  │ Google Gemini  │
│ + pgvector 0.8.6      │  │                          │  │ gemini-3.6-    │
│                       │  │ processing jobs,         │  │ flash          │
│ users, meetings,      │  │ agent runs   (planned)   │  │                │
│ transcripts,          │  │                          │  │ structured     │
│ summaries, decisions, │  └──────────────────────────┘  │ extraction     │
│ action_items,         │  ┌──────────────────────────┐  └────────────────┘
│ meeting_participants  │  │ S3   (planned, M4)       │
│ (+ chunks — planned)  │  │ audio, video, exports    │
└───────────────────────┘  └──────────────────────────┘

         EventBridge → Lambda → Agent   (planned, M11)
```

## 2. Component responsibilities

| Component | Responsibility | Does **not** do |
|---|---|---|
| `app/main.py` | Application factory, middleware order, exception handler registration | Business logic |
| `app/core/config.py` | Typed settings from repo-root `.env`; derives connection URLs; API key as `SecretStr` | Read environment ad hoc elsewhere |
| `app/core/logging.py` | JSON/console formatters, request-id context, secret redaction | Decide log levels per route |
| `app/core/middleware.py` | Assign/propagate `X-Request-ID`, log every request outcome | Authentication |
| `app/core/security.py` | Argon2id hashing, JWT issue/verify | Database access |
| `app/core/deps.py` | `DbSession`, `CurrentUser` dependencies | Authorization decisions |
| `app/core/exceptions.py` | Domain error types → one JSON error envelope | Raise errors itself |
| `app/services/authorization.py` | **The** meeting access rule, plus action-item and decision checks that delegate to it | HTTP concerns |
| `app/services/llm/base.py` | Provider-neutral `LLMProvider` contract, result types, error hierarchy | Know any vendor |
| `app/services/llm/gemini.py` | Gemini calls: schema-constrained JSON, retries, error translation, local validation | Business logic |
| `app/services/llm/factory.py` | Build the configured provider; the FastAPI dependency tests override | Anything else |
| `app/services/prompts.py` | Versioned system instruction and prompt builder | Call the model |
| `app/services/intelligence.py` | The M2 pipeline: cache check, status transitions, normalisation, persistence | HTTP, vendor details |
| `app/services/grounding.py` | Verify evidence quotes exist in the transcript | Decide what to keep |
| `app/services/dynamo.py` | DynamoDB client, dispatched off the event loop | Business data |
| `app/db/models/` | SQLAlchemy ORM definitions | Queries |
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

Any `AppError` raised anywhere, including the LLM error classes, is converted into:

```json
{"error": {"code": "llm_rate_limited", "message": "...", "request_id": "..."}}
```

Unhandled exceptions are logged with a traceback and returned as a generic 500
carrying only the request id: never a stack trace or SQL fragment.

## 4. AI processing pipeline (M2)

```
PUT  /meetings/{id}/transcript      store text, SHA-256, word count
POST /meetings/{id}/process
  │
  ├─ authorize (WRITE) ─ transcript exists? ─ no ─► 409 transcript_missing
  │
  ├─ cache: summary.transcript_sha256 == transcript.content_sha256
  │         AND prompt_version AND model unchanged ─ yes ─► return stored (no LLM call)
  │
  ├─ meeting.status = PROCESSING ; COMMIT   (releases DB connection during LLM call)
  │
  ├─ LLMProvider.generate_structured(MeetingExtraction)
  │     Gemini, response schema, temperature 0.1
  │     retries: 429 / 5xx / network, exponential backoff + jitter
  │     one re-roll on schema-invalid JSON
  │     local Pydantic validation (trust boundary)
  │
  ├─ normalise_extraction()        deterministic Python — ADR 0007
  │     trim, de-duplicate, drop blanks
  │     parse deadlines; discard implausible dates
  │     verify evidence quotes against the transcript
  │     add unlisted owners to participants
  │
  ├─ ONE transaction: delete old results → insert participants, decisions,
  │  action items (owners linked), summary with provenance → status COMPLETED
  │
  └─ on any failure: ROLLBACK, status FAILED, error envelope
        LLMRateLimitError / LLMUnavailableError / LLMNotConfiguredError → 503
        LLMResponseError / LLMRequestError                               → 502
```

**Measured** (live, `gemini-3.6-flash`, 319-word transcript): 19.5 s end to
end, 882 input / 495 output tokens; cache hit 211 ms.

**Prompt-injection defence** is layered: the transcript is fenced between
explicit delimiters and declared untrusted in the system instruction; output is
schema-constrained, so an injected instruction cannot change the response
shape; and nothing the model returns is executed. The test fixture contains an
injection line, verified in live testing not to produce an action item.

## 5. Data model

```
users 1──N meetings 1──1 transcripts
              │
              ├──1 summaries            (provenance: provider, model, prompt_version,
              │                          transcript_sha256, tokens, latency)
              ├──N meeting_participants ──0..1 users   (user_id, for M8 follow-ups)
              ├──N decisions
              └──N action_items ──0..1 meeting_participants  (owner_participant_id)
```

| Table | Key columns | Notes |
|---|---|---|
| `users` | email (unique), password_hash, is_active | |
| `meetings` | owner_id → users (CASCADE), meeting_date, source_type, status | status: created / processing / completed / failed |
| `transcripts` | meeting_id (unique, CASCADE), content, content_sha256, char/word count, source | One per meeting, replaced in place |
| `summaries` | meeting_id (unique, CASCADE), summary_text, key_points (JSONB), provenance | JSONB: always read and written whole |
| `meeting_participants` | meeting_id (CASCADE), display_name, name_key, user_id (SET NULL) | UNIQUE (meeting_id, name_key) |
| `decisions` | meeting_id (CASCADE), position, decision_text, evidence_quote, evidence_verified, status | status: open / resolved / superseded |
| `action_items` | meeting_id (CASCADE), task, owner_name, owner_participant_id (SET NULL), deadline, deadline_text, priority, status, evidence_* | status: pending / in_progress / done / cancelled |

**Indexes worth knowing**

| Index | Serves |
|---|---|
| `ix_meetings_owner_id_meeting_date` (owner_id, meeting_date DESC) | Dashboard listing — index scan, no sort |
| `ix_action_items_status_deadline` (status, deadline) | Overdue view now; the M8 agent's primary scan later |
| `ix_decisions_status` | Open-decision queries (M8) |
| FK indexes on every `meeting_id` | Per-meeting reads and cascade deletes |

**Design choices** (details inline in the models):

- Enumerations are `VARCHAR + CHECK`, not native PostgreSQL `ENUM`s, which
  cannot gain a value inside a transaction block.
- A fixed constraint-naming convention lets Alembic generate reversible
  downgrades. Both migrations have been round-tripped.
- `owner_name` and `deadline_text` keep the original wording beside the linked
  or resolved value, so a wrong resolution is visible and correctable.
- Deleting a meeting cascades to all derived data at the database level.

**Planned:** `meeting_chunks` with `embedding vector(384)` (M6); `agent_alerts`,
`agent_followups` (M8); `meeting_shares` when sharing is implemented (ADR 0004).

## 6. Authentication and authorization

**Authentication.** OAuth2 password flow. `POST /api/v1/auth/login` takes a form
body and returns a JWT (HS256, 60-minute expiry; `sub`, `iat`, `exp`, `jti`,
`type`). The token is re-checked against the database on every request.

**Authorization.** Ownership only (ADR 0004), through three functions that share
one rule:

| Resource | Function | Mechanism |
|---|---|---|
| Meeting and everything under `/meetings/{id}/…` | `authorize_meeting_access` | owner check |
| Action item by id | `authorize_action_item_access` | loads item → delegates to the meeting check |
| Decision by id | `authorize_decision_access` | loads decision → delegates to the meeting check |
| Cross-meeting lists (`GET /action-items`) | query itself | `JOIN meetings WHERE owner_id = :me` |

Every "not permitted" answer is **404**, identical to "does not exist", so ids
cannot be enumerated. Tested for all 8 meeting-scoped intelligence endpoints
and both PATCH endpoints, and a rejected intruder's request is shown never to
reach the LLM.

## 7. Security measures in place

| Measure | Where |
|---|---|
| Argon2id password hashing, transparent rehash | `core/security.py` — ADR 0005 |
| Login does not reveal whether an email is registered | `api/v1/auth.py` |
| Ownership from the token, never the request body | `api/v1/meetings.py` |
| 404-not-403 on foreign resources | `services/authorization.py` |
| Gemini key held as `SecretStr`; redacted from logs | `core/config.py`, `core/logging.py` |
| Transcript text never logged (only length and hash prefix) | `api/v1/intelligence.py` |
| Transcript size bounded (`TRANSCRIPT_MAX_CHARS`) | `schemas/transcript.py` |
| NUL bytes rejected with 422 (PostgreSQL TEXT cannot store them) | `schemas/transcript.py` |
| Explicit `null` on NOT NULL fields rejected with 422 (`PartialUpdate`) | `schemas/common.py` |
| Prompt-injection defences | `services/prompts.py` — §4 |
| Provider errors never leak SDK messages to clients | `services/llm/gemini.py` |
| Generic 500 body — no stack traces or SQL | `core/exceptions.py` |
| API docs disabled in production | `main.py` |

**Known gaps, deliberately deferred:** no rate limiting on login or `/process`
(needed before public deployment); no refresh tokens; no password reset.

## 8. Configuration

A single `.env` at the repository root serves Docker Compose and the backend.
Settings are typed, so a missing or malformed value fails at start-up.

LLM settings: `GEMINI_API_KEY`, `GEMINI_MODEL` (pinned — no `-latest` aliases,
for reproducible evaluation), `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`,
`TRANSCRIPT_MAX_CHARS`.

## 9. Local port allocation

| Port | Service | Note |
|---|---|---|
| 5432 | PostgreSQL 16 + pgvector | |
| 8001 | DynamoDB Local | Container listens on 8000 |
| 8010 | FastAPI | 8000 is occupied by an unrelated local project |

## 10. Testing strategy

| Layer | How | Files |
|---|---|---|
| Pure logic | Unit tests, no DB or network | `test_normalisation.py` |
| LLM provider | SDK stubbed; retry and error mapping | `test_gemini_provider.py` |
| API + DB | Real routes and test database; LLM replaced by `FakeLLMProvider` via dependency override | `test_processing.py`, `test_action_items.py`, `test_auth.py`, `test_meetings.py`, `test_health.py` |
| Real provider | Opt-in (`RUN_LIVE_LLM_TESTS=1 pytest -m live`) | `tests/live/` |

- Tests run against a dedicated `minuteai_test` database, with schema applied
  by the real Alembic migrations on every run.
- Each test runs in a transaction rolled back afterwards
  (`join_transaction_mode="create_savepoint"`).
- `FakeLLMProvider` is test-only. It round-trips its output through JSON so the
  same validation path runs, and its evidence quotes are real transcript
  excerpts, so grounding is exercised rather than stubbed.

## 11. Architecture decision records

| ADR | Decision |
|---|---|
| [0001](adr/0001-polyglot-persistence.md) | PostgreSQL + DynamoDB + S3, each for one job |
| [0002](adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector, not Pinecone/Chroma/Qdrant |
| [0003](adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](adr/0004-ownership-only-authorization-in-m1.md) | Ownership-only authorization, sharing as extension point |
| [0005](adr/0005-argon2-over-bcrypt.md) | Argon2id via argon2-cffi |
| [0006](adr/0006-gemini-as-initial-llm-provider.md) | Google Gemini as the initial LLM and transcription provider |
| [0007](adr/0007-structured-extraction-with-deterministic-validation.md) | Structured LLM extraction with deterministic post-processing |

Milestone status: [PROJECT_STATUS.md](PROJECT_STATUS.md).

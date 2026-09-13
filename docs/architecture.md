# MinuteAI — Architecture

> Living document. Updated as each milestone lands.
> Current state: **M1 complete**. Sections marked *(planned)* are not built yet.

## 1. System overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  React SPA  (planned, M5)                                                │
│  Login · Dashboard · Meeting detail · Ask (RAG) · Agent alerts           │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ HTTPS + JWT Bearer
┌───────────────▼──────────────────────────────────────────────────────────┐
│  FastAPI backend                        [M1: BUILT]                      │
│                                                                          │
│  middleware   RequestIDMiddleware · CORS                                 │
│  api/v1       auth · meetings          (+ jobs, rag, agent — planned)    │
│  core         config · logging · security · deps · exceptions            │
│  services     authorization · dynamo   (+ llm, rag, agent — planned)     │
│  db           models · session · migrations                              │
└───┬───────────────────────────┬──────────────────────────────────────────┘
    │                           │
┌───▼───────────────────┐  ┌────▼─────────────────────┐  ┌────────────────┐
│ PostgreSQL 16         │  │ DynamoDB                 │  │ S3   (planned) │
│ + pgvector 0.8.6      │  │ (Local in dev)           │  │                │
│                       │  │                          │  │ audio, video,  │
│ users, meetings       │  │ processing jobs,         │  │ transcripts,   │
│ (+ transcripts,       │  │ agent runs   (planned)   │  │ exports        │
│  summaries, chunks    │  │                          │  │                │
│  — planned)           │  │                          │  │                │
└───────────────────────┘  └──────────────────────────┘  └────────────────┘

         EventBridge → Lambda → Agent   (planned, M11)
```

## 2. Component responsibilities

| Component | Responsibility | Does **not** do |
|---|---|---|
| `app/main.py` | Application factory, middleware order, exception handler registration | Business logic |
| `app/core/config.py` | Typed settings from repo-root `.env`; derives connection URLs | Read environment ad hoc elsewhere |
| `app/core/logging.py` | JSON/console formatters, request-id context, secret redaction | Decide log levels per route |
| `app/core/middleware.py` | Assign/propagate `X-Request-ID`, log every request outcome | Authentication |
| `app/core/security.py` | Argon2id hashing, JWT issue/verify | Database access |
| `app/core/deps.py` | `DbSession`, `CurrentUser` dependencies | Authorization decisions |
| `app/core/exceptions.py` | Domain error types → one JSON error envelope | Raise errors itself |
| `app/services/authorization.py` | **The** meeting access rule | HTTP concerns |
| `app/services/dynamo.py` | DynamoDB client, dispatched off the event loop | Business data |
| `app/db/models/` | SQLAlchemy ORM definitions | Queries |
| `app/api/v1/` | HTTP contract, status codes, Pydantic validation | Direct SQL against `meetings` by id |

## 3. Request lifecycle

```
HTTP request
  → RequestIDMiddleware      assign/propagate X-Request-ID, start timer
  → CORSMiddleware           origin check
  → route                    Pydantic validates the body
  → get_current_user         decode JWT → load User → check is_active
  → authorize_meeting_access (meeting-scoped routes only) → 404 if not permitted
  → handler                  business logic, explicit commit
  → response_model           serialise (password_hash structurally absent)
  → RequestIDMiddleware      log method/path/status/duration, set response header
```

Any `AppError` raised anywhere is converted by `app_error_handler` into:

```json
{"error": {"code": "not_found", "message": "Meeting not found.", "request_id": "..."}}
```

Unhandled exceptions are logged with a traceback and returned as a generic 500
carrying only the request id — never a stack trace or SQL fragment.

## 4. Data model (current)

```
users                                  meetings
──────────────────────────             ──────────────────────────────────
id            uuid PK                  id            uuid PK
email         varchar(320) UNIQUE      owner_id      uuid FK → users.id
password_hash varchar(255)                           ON DELETE CASCADE
full_name     varchar(255)             title         varchar(255)
is_active     boolean                  description   text NULL
created_at    timestamptz              meeting_date  timestamptz
updated_at    timestamptz              source_type   varchar(16) CHECK
                                       status        varchar(16) CHECK
        1 ──────────────< N            created_at    timestamptz
                                       updated_at    timestamptz
```

**Indexes**

| Index | Serves |
|---|---|
| `ix_users_email` (unique) | Login lookup; enforces one account per address |
| `ix_meetings_owner_id` | Foreign-key joins |
| `ix_meetings_owner_id_meeting_date` (owner_id, meeting_date DESC) | Dashboard listing — index scan, no sort |
| `ix_meetings_status` | Status filter; the pending-work scan in M3 |

**Enumerations** are rendered as `VARCHAR + CHECK`, not native PostgreSQL
`ENUM` types. A PostgreSQL enum cannot gain a value inside a transaction block,
which makes every future status addition an awkward migration; a CHECK
constraint is simply dropped and recreated.

**Constraint naming** follows a fixed convention (`pk_`, `fk_`, `ix_`, `uq_`,
`ck_`) declared on the metadata, so Alembic can generate reversible
`downgrade()` steps rather than depending on names PostgreSQL invents.

**Planned tables:** `meeting_participants`, `transcripts`, `summaries`,
`decisions`, `action_items` (M2); `meeting_chunks` with `embedding vector(384)`
(M6); `agent_alerts`, `agent_followups` (M8); `meeting_shares` (when sharing is
implemented — see ADR 0004).

## 5. Authentication and authorization

**Authentication.** OAuth2 password flow. `POST /api/v1/auth/login` takes a form
body (`username`, `password`) and returns a JWT (HS256, 60-minute expiry,
carrying `sub`, `iat`, `exp`, `jti`, `type`). The standard form shape is what
makes Swagger UI's *Authorize* button work.

The token is re-checked against the database on every request, so deactivating
or deleting an account takes effect immediately rather than at token expiry.

**Authorization.** Ownership only, through `authorize_meeting_access()`. A
meeting the caller does not own returns **404**, identical to a non-existent
meeting, so meeting ids cannot be enumerated. See ADR 0004.

## 6. Security measures in place (M1)

| Measure | Where |
|---|---|
| Argon2id password hashing | `core/security.py` — ADR 0005 |
| Transparent hash upgrade on login | `api/v1/auth.py` |
| Login does not reveal whether an email is registered | `api/v1/auth.py` |
| Registration race handled by the UNIQUE constraint, returns 409 not 500 | `api/v1/auth.py` |
| Ownership taken from the token, never the request body | `api/v1/meetings.py` |
| 404-not-403 to prevent id enumeration | `services/authorization.py` |
| Secret redaction in logs (recursive, incl. nested dicts) | `core/logging.py` |
| Generic 500 body — no stack traces or SQL to the client | `core/exceptions.py` |
| API docs disabled when `APP_ENV=production` | `main.py` |
| Password length bounded (8–128) against Argon2 cost amplification | `schemas/auth.py` |
| `.env` git-ignored; `.env.example` committed | repo root |
| Parameterised queries throughout (SQLAlchemy) | all DB access |

**Known gaps, deliberately deferred:** no rate limiting on login (needed before
any public deployment), no refresh tokens, no email verification, no password
reset, no account lockout.

## 7. Configuration

A single `.env` at the repository root serves both Docker Compose (native
variable substitution) and the backend (`pydantic-settings`). One password, one
place. Settings are typed, so a missing or malformed value fails at start-up
rather than inside a request.

## 8. Local port allocation

| Port | Service | Note |
|---|---|---|
| 5432 | PostgreSQL 16 + pgvector | |
| 8001 | DynamoDB Local | Container listens on 8000, mapped to 8001 |
| 8010 | FastAPI | 8000 avoided — occupied by an unrelated local project |

## 9. Testing strategy

- The test suite runs against a dedicated `minuteai_test` database, created by
  the Postgres container's init script.
- Schema is applied by running the **real Alembic migrations**, so every test
  run exercises the migrations rather than assuming they work.
- Each test runs in a transaction rolled back afterwards
  (`join_transaction_mode="create_savepoint"`), so application code can call
  `commit()` normally while the database ends each test unchanged.
- Authorization tests are first-class: a second user must receive 404 on read,
  update, and delete of another user's meeting.

## 10. Milestone status

See [PROJECT_STATUS.md](PROJECT_STATUS.md).

## 11. Architecture decision records

| ADR | Decision |
|---|---|
| [0001](adr/0001-polyglot-persistence.md) | PostgreSQL + DynamoDB + S3, each for one job |
| [0002](adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector, not Pinecone/Chroma/Qdrant |
| [0003](adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](adr/0004-ownership-only-authorization-in-m1.md) | Ownership-only authorization, sharing as extension point |
| [0005](adr/0005-argon2-over-bcrypt.md) | Argon2id via argon2-cffi |
| [0006](adr/0006-gemini-as-initial-llm-provider.md) | Google Gemini as the initial LLM and transcription provider |

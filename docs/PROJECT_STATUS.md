# MinuteAI — Project Status

**Last updated:** 2026-09-12
**Current milestone:** M1 — Foundation ✅ **COMPLETE**
**Next milestone:** M2 — Text-first AI intelligence 🔴 **BLOCKED — needs `GROQ_API_KEY`**

---

## Milestone progress

| # | Milestone | Status |
|---|---|---|
| **M1** | Foundation — Docker, Postgres+pgvector, DynamoDB Local, FastAPI, auth, meeting CRUD | ✅ Complete |
| M2 | Text-first AI intelligence (summary / decisions / action items) | 🔴 Blocked — credential |
| M3 | Async processing + DynamoDB job state | ⬜ Not started |
| M4 | Audio upload + S3 + transcription | 🔴 Blocked — AWS + Groq |
| M5 | React frontend | ⬜ Not started |
| M6 | Embeddings + pgvector | ⬜ Not started (dependency verified ✅) |
| M7 | Cross-meeting RAG | ⬜ Not started |
| M8 | Agent automation | ⬜ Not started |
| M9 | Agent UI + human approval | ⬜ Not started |
| M10 | AWS deployment | 🔴 Blocked — AWS account |
| M11 | Lambda + EventBridge | 🔴 Blocked — AWS account |
| M12 | Testing + evaluation + finalisation | ⬜ Not started |

---

## M1 — completed features

### Infrastructure
- [x] Repository at `C:\Users\madhan\OneDrive\Desktop\adv sql`
      (relocated 2026-09-12 at user request, after the `C:\dev` copy was
      accidentally deleted and recovered from the Recycle Bin with git history
      and `.env` intact; a snapshot remains at `C:\dev\minuteai-BACKUP-2026-09-12`)
- [ ] NOTE: living inside OneDrive. If `pip install` ever fails with a file-lock
      or permission error, pause OneDrive sync for the duration of the install.
- [x] Python 3.12.10 virtual environment
- [x] Dependencies declared in `pyproject.toml`, pinned in `requirements.lock.txt` (51 packages)
- [x] `docker-compose.yml` — PostgreSQL 16.15 + pgvector 0.8.6, DynamoDB Local
- [x] Postgres healthcheck (`pg_isready`) so migrations cannot race start-up
- [x] Test database `minuteai_test` auto-created by container init script
- [x] `.gitignore`, `.gitattributes` (LF enforcement for container scripts)
- [x] `.env` / `.env.example`, secrets generated locally and git-ignored

### Backend
- [x] FastAPI application factory with lifespan management
- [x] Typed configuration via `pydantic-settings` (fails at start-up, not mid-request)
- [x] Structured logging — JSON and console formatters, zero dependencies
- [x] Recursive secret redaction in logs (verified: nested dicts included)
- [x] `RequestIDMiddleware` — generates or propagates `X-Request-ID`, logs every request
- [x] Consistent error envelope for all failure modes
- [x] CORS configured from settings
- [x] API docs auto-disabled when `APP_ENV=production`

### Persistence
- [x] Async SQLAlchemy 2.0 + asyncpg, `lazy="raise"` on all relationships
- [x] `users` and `meetings` models with deterministic constraint naming
- [x] Alembic (async template), migration `0001`
- [x] pgvector extension enabled in migration — **M6 dependency proven in M1**
- [x] Migration verified reversible (downgrade → base → upgrade → head)
- [x] Composite index `(owner_id, meeting_date DESC)` for the dashboard query

### Security
- [x] Argon2id password hashing with transparent rehash on login
- [x] JWT (HS256) with `sub`/`iat`/`exp`/`jti`/`type` claims
- [x] User re-validated against the DB on every request
- [x] `authorize_meeting_access()` — single authorization mechanism
- [x] 404-not-403 on foreign resources (prevents id enumeration)
- [x] Login cannot be used to discover registered email addresses
- [x] Ownership taken from the token, never from the request body

### API
- [x] `GET /health` — liveness, touches no dependency
- [x] `GET /health/deps` — Postgres + pgvector + DynamoDB, 503 when degraded
- [x] `POST /api/v1/auth/register`
- [x] `POST /api/v1/auth/login`
- [x] `GET /api/v1/auth/me`
- [x] `POST /api/v1/meetings`
- [x] `GET /api/v1/meetings` (pagination + status filter)
- [x] `GET /api/v1/meetings/{id}`
- [x] `PATCH /api/v1/meetings/{id}`
- [x] `DELETE /api/v1/meetings/{id}`

### Quality
- [x] **29 tests passing**, 0 warnings
- [x] `ruff check` clean; `ruff format` applied
- [x] Migrations exercised on every test run
- [x] End-to-end verification against a live uvicorn server (13-step flow)
- [x] Log inspection confirmed zero secret leakage

---

## Test status

```
29 passed in ~10s
  tests/test_health.py     5   health, request-id propagation, error envelope
  tests/test_auth.py       9   register, login, token validation, enumeration guards
  tests/test_meetings.py  15   CRUD, pagination, validation, cross-user isolation
```

**Authorization coverage:** a second user receives 404 on GET, PATCH, and DELETE
of another user's meeting, and the target record is verified unchanged.

---

## Verified facts

| Fact | Evidence |
|---|---|
| pgvector works on PostgreSQL 16 | `SELECT '[1,2,3]'::vector <=> '[1,2,4]'::vector` → `0.0085…`; extension 0.8.6 |
| Migration is reversible | downgrade → 0 tables; upgrade → 2 tables |
| DynamoDB Local reachable | `list_tables` returns `[]` via boto3 |
| Secrets never logged | `grep` for password/token over full server log → 0 hits |
| Cross-user isolation holds live | Bob + valid token + Alice's meeting id → 404 |

---

## Known issues / deferred work

| Item | Severity | Plan |
|---|---|---|
| No rate limiting on `/auth/login` | Medium | Required before any public deployment (M10) |
| No refresh tokens, password reset, or email verification | Low | Out of MVP scope |
| `meeting_shares` not implemented | By design | ADR 0004 — extension point ready |
| pgvector on **RDS** not yet verified | Medium | Must verify before M10 depends on it |
| DynamoDB is advisory, not transactional with Postgres | Low | Accepted; ADR 0001 |
| `backend/.venv` is 3.12-specific | Low | Recreate if Python version changes |

---

## Technical decisions

| ADR | Decision |
|---|---|
| [0001](adr/0001-polyglot-persistence.md) | Polyglot persistence — PostgreSQL + DynamoDB + S3 |
| [0002](adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector over a dedicated vector database |
| [0003](adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](adr/0004-ownership-only-authorization-in-m1.md) | Ownership-only authorization in M1 |
| [0005](adr/0005-argon2-over-bcrypt.md) | Argon2id via argon2-cffi |

Smaller decisions recorded inline in `docs/architecture.md`: VARCHAR+CHECK
instead of native PostgreSQL enums; UUID primary keys; single root `.env`;
`docker-compose.yml` at repo root; health split into liveness and readiness.

---

## Deployment status

Local development only. No cloud resources provisioned. No AWS account
connected.

---

## 🔴 Blocker — action required to continue to M2

M2 is the first milestone that calls an external AI provider. Everything that
can be built without the key has been built; the pipeline itself cannot be
implemented honestly without one (no simulated LLM responses — project Rule 5).

**What is needed:** a Groq API key.

1. **Obtain:** sign in at <https://console.groq.com> → *API Keys* → *Create API
   Key*. The free tier is sufficient for development.
2. **Place it:** add to `C:\dev\minuteai\.env`
   ```
   GROQ_API_KEY=gsk_your_key_here
   ```
   `.env` is git-ignored; the key will not be committed.
3. **Verify:** after M2's LLM service exists, `GET /health/deps` will include a
   `groq` check.

**What proceeds automatically once the key is present:** LLM service interface
with Groq as the first implementation, Pydantic schemas for structured
extraction, `transcripts` / `summaries` / `decisions` / `action_items` tables
and migration, the transcript → summary/decisions/actions pipeline, the
processing endpoint, and tests.

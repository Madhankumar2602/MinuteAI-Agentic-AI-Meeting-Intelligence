# MinuteAI

**Cloud-Based AI Meeting Intelligence and Productivity System using Polyglot
Persistence with Retrieval-Augmented Generation and Agentic Automation**

Turns meeting audio, video, or transcripts into structured, queryable, and
actionable knowledge: summaries, decisions, action items, cross-meeting
semantic Q&A, and proactive follow-up detection.

> **Status: M2 — Text-first AI intelligence ✅ complete.** 80 tests passing (+2 opt-in live tests).
> See [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

---

## Architecture at a glance

| Concern | Technology | Why |
|---|---|---|
| API | FastAPI, Python 3.12 | Async, typed, self-documenting |
| Relational store | PostgreSQL 16 | Source of truth for all business data |
| Vector search | pgvector (inside PostgreSQL) | Keeps ACL filtering and vectors in one transactional store — [ADR 0002](docs/adr/0002-pgvector-over-dedicated-vector-db.md) |
| Workflow state | DynamoDB | Schema-fluid job state with TTL expiry — [ADR 0001](docs/adr/0001-polyglot-persistence.md) |
| Object storage | Amazon S3 *(M4)* | Audio, video, transcripts, exports |
| LLM + transcription | Google Gemini *(M2)* | One provider behind a swappable interface — [ADR 0006](docs/adr/0006-gemini-as-initial-llm-provider.md) |
| Auth | JWT + Argon2id | [ADR 0005](docs/adr/0005-argon2-over-bcrypt.md) |

Full detail: [docs/architecture.md](docs/architecture.md).

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.12** | Not 3.13/3.14 — the M4/M6 ML stack lacks wheels for those. `winget install --id Python.Python.3.12 --exact` |
| **Docker Desktop** | Must be running before `docker compose up` |
| **Git** | |

Ports **5432**, **8001**, and **8010** must be free.

> **Windows note:** if Anaconda is installed, `python` on your PATH is probably
> Anaconda's. Always create the venv with `py -3.12`, never `python -m venv`.

---

## Setup

### 1. Clone and configure

```bash
git clone <your-repo-url> C:\dev\minuteai
```

Create your `.env` from the template:

```bash
cp .env.example .env
```

Then edit `.env` and set two values:

- `POSTGRES_PASSWORD` — any strong local password
- `JWT_SECRET` — at least 32 characters; generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`
- `GEMINI_API_KEY` — from <https://aistudio.google.com> → *Get API key*.
  Only needed for processing; everything else runs without it.

`.env` is git-ignored and must never be committed.

### 2. Start the databases

```bash
docker compose up -d
```

Wait for Postgres to report healthy:

```bash
docker compose ps
```

### 3. Create the Python environment

```bash
py -3.12 -m venv backend/.venv
```
```bash
.\backend\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
(Git Bash users: `source backend/.venv/Scripts/activate`.)

```bash
python -m pip install --upgrade pip
```
```bash
pip install -e "backend[dev]"
```

### 4. Apply migrations

```bash
alembic -c backend/alembic.ini upgrade head
```

### 5. Run the API

```bash
uvicorn app.main:app --reload --port 8010 --app-dir backend
```

Open <http://localhost:8010/docs>.

---

## Verify the installation

```bash
curl http://localhost:8010/health/deps
```

Expected — note `pgvector=yes` and the Gemini model:

```json
{"status":"ok","checks":{
  "postgres":{"healthy":true,"detail":"reachable (pgvector=yes)"},
  "dynamodb":{"healthy":true,"detail":"reachable (0 table(s))"},
  "gemini":{"healthy":true,"detail":"reachable (model=gemini-3.6-flash)"}}}
```

Run the test suite (from `backend/`):

```bash
pytest
```

Expected: `80 passed, 2 skipped`. The two skipped tests call the real Gemini
API and are opt-in:

```bash
RUN_LIVE_LLM_TESTS=1 pytest -m live
```

Lint and format:

```bash
ruff check app tests
```

---

## Trying it by hand

The quickest path is Swagger at <http://localhost:8010/docs>:

1. `POST /api/v1/auth/register` — create an account.
2. Click **Authorize** (top right), enter the same email and password.
3. Every endpoint below is now callable with your token attached.

Or with curl:

```bash
curl -X POST http://localhost:8010/api/v1/auth/register -H "Content-Type: application/json" -d '{"email":"you@example.com","password":"a-good-password","full_name":"You"}'
```

```bash
curl -X POST http://localhost:8010/api/v1/auth/login -d "username=you@example.com&password=a-good-password"
```

```bash
curl -X POST http://localhost:8010/api/v1/meetings -H "Authorization: Bearer PASTE_TOKEN" -H "Content-Type: application/json" -d '{"title":"Sprint planning","meeting_date":"2026-09-11T14:00:00Z"}'
```

Add a transcript, then process it. A sample lives at
`backend/tests/fixtures/transcripts/platform_sync.txt`.

```bash
curl -X PUT http://localhost:8010/api/v1/meetings/MEETING_ID/transcript -H "Authorization: Bearer PASTE_TOKEN" -H "Content-Type: application/json" -d '{"content":"Priya: Karthik, please prepare the runbook by Friday.
Karthik: Yes, I will have it ready by Friday."}'
```

```bash
curl -X POST http://localhost:8010/api/v1/meetings/MEETING_ID/process -H "Authorization: Bearer PASTE_TOKEN"
```

Processing takes roughly 5-30 seconds in M2. Calling it again on an unchanged
transcript returns the stored result instantly (`"cached": true`).

---

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/health/deps` | – | Readiness: Postgres, pgvector, DynamoDB |
| POST | `/api/v1/auth/register` | – | Create account |
| POST | `/api/v1/auth/login` | – | Obtain JWT |
| GET | `/api/v1/auth/me` | ✔ | Current user |
| POST | `/api/v1/meetings` | ✔ | Create meeting |
| GET | `/api/v1/meetings` | ✔ | List own meetings (paginated) |
| GET | `/api/v1/meetings/{id}` | ✔ | Get one |
| PATCH | `/api/v1/meetings/{id}` | ✔ | Partial update |
| DELETE | `/api/v1/meetings/{id}` | ✔ | Delete (cascades to all derived data) |
| PUT | `/api/v1/meetings/{id}/transcript` | ✔ | Add or replace the transcript |
| GET | `/api/v1/meetings/{id}/transcript` | ✔ | Get the transcript |
| POST | `/api/v1/meetings/{id}/process` | ✔ | Run AI extraction (`?force=true` to re-run) |
| GET | `/api/v1/meetings/{id}/intelligence` | ✔ | Summary, participants, decisions, action items |
| GET | `/api/v1/meetings/{id}/summary` | ✔ | Summary with provenance and `is_stale` |
| GET | `/api/v1/meetings/{id}/decisions` | ✔ | Decisions |
| GET | `/api/v1/meetings/{id}/action-items` | ✔ | Action items |
| GET | `/api/v1/meetings/{id}/participants` | ✔ | Participants |
| GET | `/api/v1/action-items` | ✔ | All my action items (`status`, `overdue`, `meeting_id`) |
| PATCH | `/api/v1/action-items/{id}` | ✔ | Correct task / status / deadline / priority |
| PATCH | `/api/v1/decisions/{id}` | ✔ | Correct text / status |

Errors share one envelope:

```json
{"error": {"code": "not_found", "message": "Meeting not found.", "request_id": "..."}}
```

The `request_id` also appears on the `X-Request-ID` response header and in
every log line for that request.

---

## Project layout

```
minuteai/
├─ docker-compose.yml        Postgres+pgvector, DynamoDB Local
├─ .env / .env.example       single config source for compose AND the backend
├─ docs/
│  ├─ architecture.md
│  ├─ PROJECT_STATUS.md
│  └─ adr/                   architecture decision records
├─ infra/postgres/init/      container init scripts
└─ backend/
   ├─ pyproject.toml         dependencies + ruff/pytest config
   ├─ requirements.lock.txt  exact versions known to work
   ├─ alembic.ini
   ├─ app/
   │  ├─ main.py             application factory
   │  ├─ core/               config, logging, middleware, security, deps, exceptions
   │  ├─ db/                 models, session, migrations
   │  ├─ schemas/            Pydantic request/response models
   │  ├─ api/v1/             routes
   │  └─ services/           authorization, intelligence, grounding, prompts,
   │                         dynamo, llm/ (provider contract + Gemini)
   └─ tests/
```

---

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `No suitable Python runtime found` | 3.12 missing, or PATH not refreshed | Install it, then open a new terminal |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError: app` | Installed without `-e` | `pip install -e ".[dev]"` from `backend/` |
| `connection refused` on 5432 | Containers not up | `docker compose up -d`, wait for healthy |
| `address already in use` on 8010 | Another process holds the port | `netstat -ano \| findstr :8010` |
| `MissingGreenlet` | Lazy-loaded a relationship | Load it explicitly with `selectinload()` — [ADR 0003](docs/adr/0003-async-sqlalchemy.md) |
| `/process` returns 503 `llm_not_configured` | `GEMINI_API_KEY` empty or invalid | Set it in `.env`, restart the API |
| `/process` returns 503 `llm_rate_limited` | Gemini free-tier quota reached | Wait and retry; unchanged transcripts are served from cache |
| Tests fail on a fresh clone | `minuteai_test` missing | `docker compose down -v && docker compose up -d` (⚠️ destroys local data) |

---

## Milestones

- **M1** ✅ Foundation — Docker, Postgres+pgvector, DynamoDB Local, FastAPI, auth, meeting CRUD
- **M2** ✅ Meeting intelligence — Gemini extraction of summary, decisions, action items, with evidence verification
- M3 Async pipeline + DynamoDB job state
- M4 Audio upload + S3 + transcription
- M5 React UI
- M6 Embeddings + pgvector
- M7 Cross-meeting RAG
- M8 Agent (tools, alerts, follow-up drafts)
- M9 Agent UI + human approval
- M10 AWS deployment
- M11 Lambda + EventBridge scheduling
- M12 Evaluation + writeup

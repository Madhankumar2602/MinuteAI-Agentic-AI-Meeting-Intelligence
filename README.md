# MinuteAI

**Cloud-Based AI Meeting Intelligence and Productivity System using Polyglot
Persistence with Retrieval-Augmented Generation and Agentic Automation**

Turns meeting audio, video, or transcripts into structured, queryable, and
actionable knowledge: summaries, decisions, action items, cross-meeting
semantic Q&A, and proactive follow-up detection.

> **Status: M1 — Foundation ✅ complete.** 29 tests passing.
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
| LLM + transcription | Groq *(M2)* | One provider behind a swappable interface |
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

Expected — note `pgvector=yes`:

```json
{"status":"ok","checks":{
  "postgres":{"healthy":true,"detail":"reachable (pgvector=yes)"},
  "dynamodb":{"healthy":true,"detail":"reachable (0 table(s))"}}}
```

Run the test suite (from `backend/`):

```bash
pytest
```

Expected: `29 passed`.

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

---

## API surface (M1)

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
| DELETE | `/api/v1/meetings/{id}` | ✔ | Delete |

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
   │  └─ services/           authorization, dynamo
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
| Tests fail on a fresh clone | `minuteai_test` missing | `docker compose down -v && docker compose up -d` (⚠️ destroys local data) |

---

## Milestones

- **M1** ✅ Foundation — Docker, Postgres+pgvector, DynamoDB Local, FastAPI, auth, meeting CRUD
- **M2** 🔴 Meeting intelligence (summary / decisions / action items) — *needs `GROQ_API_KEY`*
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

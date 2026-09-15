# MinuteAI — AI Meeting Intelligence

**Cloud-based AI meeting intelligence and productivity system using polyglot
persistence, retrieval-augmented generation, and agentic AI.**

MinuteAI turns any meeting into **structured Minutes of Meeting (MOM)** and a
**professional PDF**. Give it meeting notes, a transcript, an audio recording, or
a video. It transcribes when needed, extracts what matters with Gemini, validates
the result, and lets you search and ask questions across all your meetings, with
every answer traced back to its source.

---

## The core idea

```
 Meeting notes / description ─┐
 Transcript ──────────────────┤
 Audio ───────────────────────┼──► Input processing
 Video ──► audio extraction ──┘            │
                                           ▼
                          Transcription with speaker labels
                                           ▼
                          Gemini structured extraction
                                           ▼
                          Rule-based validation (evidence, owners, deadlines)
                                           ▼
                          Structured Minutes of Meeting
                 ┌─────────────────────────┼─────────────────────────┐
                 ▼                         ▼                         ▼
           Summary · keywords      Speakers · decisions      Action items · owner
           · discussion points     · pending items           · deadline · next steps
                                           ▼
                          Professional PDF ──► stored in S3 ──► view / download
                                           ▼
                 Embeddings (pgvector) ──► Semantic search ──► Ask your meetings (RAG)
```

### What the minutes contain

Meeting title · agenda / description · date and time · participants · speaker-wise
contributions · executive summary · key discussion points · keywords / topics ·
decisions · action items with **owner** and **deadline** · pending / unresolved
items · next steps · source and transcript reference with verified evidence quotes.

---

## Features

| | |
|---|---|
| **Four input types** | Meeting notes, transcripts, audio (MP3, WAV, M4A, OGG, FLAC, WebM), video (MP4, MOV, WebM). Uploads go straight from the browser to storage through presigned POST policies, with file-signature validation |
| **Video → audio** | The audio track is extracted locally (PyAV, 16 kHz mono Opus), so only speech is sent for transcription |
| **Transcription** | Gemini transcription with speaker names, exact durations, raw output archived to S3 |
| **Structured extraction** | One schema-constrained Gemini call produces the full MOM; deterministic post-processing resolves deadlines, links owners to participants, and **verifies every evidence quote against the transcript** |
| **Validation** | Rule-based review flags: tasks without owner or deadline, unverifiable evidence, unnamed speakers, edited transcripts |
| **Minutes of Meeting** | One data model feeds the API, the web view, and the PDF, and reflects user corrections immediately |
| **PDF** | A4 minutes with numbered sections, action-item table, evidence appendix, page numbers, embedded Unicode fonts; stored in S3, regenerated only when the minutes change |
| **Background processing** | DynamoDB job queue with leases, heartbeats, fencing, retries with back-off, and crash recovery |
| **Semantic search** | Transcript passages embedded locally (all-MiniLM-L6-v2 via ONNX Runtime) in PostgreSQL + pgvector, with ownership enforced inside the vector query |
| **Ask your meetings** | Retrieval-augmented answers over transcripts **and** minutes; answers cite numbered sources, citations are verified in code, and questions the meetings cannot answer get "not found" instead of a guess |
| **Web app** | React + TypeScript: dashboard, meetings, live processing progress, minutes view, PDF preview, action items by urgency, search (Ctrl K), Ask, light and dark themes, responsive layout |
| **Security** | Argon2id passwords, JWT, per-user authorization (404 for other users' data), prompt-injection fencing, secret redaction in logs, storage-backend guard that stops local development from ever reaching real AWS |

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│  React SPA (Vite · TypeScript · TanStack Query)                           │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ HTTPS + JWT                     presigned POST (uploads)
┌──────────────▼──────────────────────────────┐        │
│  FastAPI                                     │        │
│  api/v1 · services · embedded job worker     │        │
└──┬───────────────┬───────────────┬───────────┘        │
   │               │               │                    │
┌──▼────────────┐ ┌▼─────────────┐ ┌▼────────────────┐ ┌─▼──────────────────┐
│ PostgreSQL 16 │ │ DynamoDB     │ │ Google Gemini   │ │ Amazon S3          │
│ + pgvector    │ │ job queue,   │ │ transcription,  │ │ recordings, raw    │
│ source of     │ │ leases, TTL  │ │ extraction,     │ │ transcripts,       │
│ truth + HNSW  │ │              │ │ grounded answers│ │ minutes PDFs       │
└───────────────┘ └──────────────┘ └─────────────────┘ └────────────────────┘
```

**Polyglot persistence:** each store does one job. PostgreSQL holds business data
and vectors, so access control and similarity search share one transactional
query. DynamoDB holds ephemeral workflow state. S3 holds large files.

| Concern | Technology |
|---|---|
| API | FastAPI, Python 3.12, async SQLAlchemy 2.0, Alembic, Pydantic |
| Data | PostgreSQL 16 + pgvector (HNSW), DynamoDB, Amazon S3 (RustFS locally) |
| AI | Google Gemini (`google-genai`), sentence-transformers all-MiniLM-L6-v2 via ONNX Runtime |
| Media / documents | PyAV (FFmpeg), ReportLab |
| Web | React 19, TypeScript, Vite, TanStack Query, react-router |
| Quality | pytest (real PostgreSQL, DynamoDB Local, S3), Vitest + Testing Library, ruff, oxlint |
| Infrastructure | Docker Compose |

Design details: [docs/architecture.md](docs/architecture.md) · decisions and trade-offs: [docs/adr](docs/adr).

---

## Project structure

```
.
├─ docker-compose.yml          PostgreSQL + pgvector, DynamoDB Local, S3-compatible storage
├─ .env.example                single configuration file for Compose and the backend
├─ backend/
│  ├─ app/
│  │  ├─ main.py               application factory, lifespan, embedded worker
│  │  ├─ api/v1/               auth · meetings · intelligence · media · jobs · mom · search · ask · dashboard
│  │  ├─ core/                 config, security, logging, middleware, AWS client guard
│  │  ├─ db/                   models and Alembic migrations
│  │  ├─ schemas/              request/response and LLM contracts
│  │  ├─ services/
│  │  │  ├─ llm/               provider interface + Gemini implementation
│  │  │  ├─ intelligence.py    extraction pipeline and normalisation
│  │  │  ├─ grounding.py       evidence verification
│  │  │  ├─ transcription.py   recording → transcript
│  │  │  ├─ audio.py           audio extraction from video
│  │  │  ├─ mom/               minutes builder, review flags, PDF renderer, PDF storage
│  │  │  ├─ embeddings/        chunking, local model, indexing, vector search
│  │  │  ├─ rag.py             Ask your meetings
│  │  │  ├─ job_store.py       DynamoDB job queue
│  │  │  └─ storage.py         S3 access and presigned URLs
│  │  ├─ workers/              background processing worker
│  │  └─ evaluation/           offline retrieval evaluation
│  └─ tests/                   unit, integration, and opt-in live tests
├─ frontend/
│  └─ src/
│     ├─ api/                  typed client (types generated from OpenAPI), uploads
│     ├─ auth/                 session and auth context
│     ├─ components/           design system, minutes view, PDF preview, processing stepper
│     ├─ lib/                  pure helpers (formatting, citations, grouping, theme)
│     └─ pages/                dashboard, meetings, meeting detail, action items, search, ask
├─ docs/
│  ├─ architecture.md
│  ├─ PROJECT_STATUS.md
│  └─ adr/                     architecture decision records
├─ infra/                      container init scripts
└─ scripts/                    sample-audio generation
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.12** | Not 3.13/3.14 — some media and ML dependencies lack wheels for those. `winget install --id Python.Python.3.12 --exact` |
| **Docker Desktop** | Must be running before `docker compose up` |
| **Node.js 20+** | For the web app (tested with npm 11) |
| **Git** | |

Ports **5432**, **8001**, **8010**, **9000**, and **5173** must be free.

> **Windows note:** if Anaconda is installed, `python` on your PATH is probably
> Anaconda's. Always create the venv with `py -3.12`, never `python -m venv`.

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/Madhankumar2602/MinuteAI-Agentic-AI-Meeting-Intelligence.git minuteai
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

Keep `STORAGE_BACKEND=local` for development. In this mode the app will not start
unless S3 and DynamoDB point at the local containers, and it never reads
`~/.aws/credentials`, so local work cannot reach a real AWS account
([ADR 0010](docs/adr/0010-explicit-storage-backend.md)).

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

### 6. Run the web app

In a second terminal:

```bash
npm --prefix frontend install
```
```bash
npm --prefix frontend run dev
```

Open <http://localhost:5173> and create an account. The dev server proxies
`/api` and `/health` to the API on port 8010, so the API must be running.

---

## Verify the installation

```bash
curl http://localhost:8010/health/deps
```

Expected — note `pgvector=yes` and the Gemini model:

```json
{"status":"ok","checks":{
  "postgres":{"healthy":true,"detail":"reachable (pgvector=yes)"},
  "dynamodb":{"healthy":true,"detail":"reachable (jobs table ACTIVE)"},
  "s3":{"healthy":true,"detail":"reachable (bucket minuteai-dev-media)"},
  "gemini":{"healthy":true,"detail":"reachable (model=gemini-3.6-flash)"},
  "worker":{"healthy":true,"detail":"polling (active=0, completed=0, failed=0, retried=0)"}}}
```

Run the test suite (from `backend/`):

```bash
pytest
```

All tests should pass; tests marked `live` are skipped unless enabled. The first run downloads the embedding model (~90 MB) for `test_embedding_model.py`; later runs use the cache. (Docker must be running: tests use the real local Postgres, DynamoDB, and S3 containers.) The `live` tests call the real Gemini API and are opt-in:

```bash
RUN_LIVE_LLM_TESTS=1 pytest -m live
```

Retrieval smoke evaluation (offline, real model):

```bash
python -m app.evaluation.retrieval
```

Lint and format:

```bash
ruff check app tests
```

Frontend checks (from `frontend/`):

```bash
npm run typecheck
```
```bash
npm run lint
```
```bash
npm test
```
```bash
npm run build
```

After changing the API, regenerate the frontend's types (the backend venv must exist):

```bash
npm run gen:api
```

---

## Trying it by hand

The quickest path is the web app at <http://localhost:5173>. To drive the API directly, use Swagger at <http://localhost:8010/docs>:

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

This returns **202 Accepted** immediately with a `job`. The extraction runs in
the background (roughly 10-30 seconds). Poll the job until its `status` is
`COMPLETED` or `FAILED`:

```bash
curl http://localhost:8010/api/v1/jobs/JOB_ID -H "Authorization: Bearer PASTE_TOKEN"
```

Then read the results from `GET /api/v1/meetings/MEETING_ID/intelligence`.
Submitting again on an unchanged transcript returns **200** with
`"cached": true` and queues nothing.

### Uploading a recording

Recordings upload straight from the client to storage. Three calls:

```bash
curl -X POST http://localhost:8010/api/v1/meetings/MEETING_ID/media/upload-url -H "Authorization: Bearer PASTE_TOKEN" -H "Content-Type: application/json" -d '{"filename":"standup.wav","content_type":"audio/wav","size_bytes":4937822}'
```

POST the file to the returned `upload_url` as multipart form data, sending
every entry of `fields` first and the file last under the name `file`. Then
confirm, which queues transcription and extraction:

```bash
curl -X POST http://localhost:8010/api/v1/meetings/MEETING_ID/media/complete -H "Authorization: Bearer PASTE_TOKEN" -H "Content-Type: application/json" -d '{"upload_token":"PASTE_UPLOAD_TOKEN"}'
```

To create a sample recording (Windows), run
`powershell -ExecutionPolicy Bypass -File scripts\generate_sample_audio.ps1`.

### Running the worker separately

By default the worker runs inside the API process. To run it as its own process
(for example, to scale it independently), set `WORKER_EMBEDDED=false` and start:

```bash
python -m app.workers.processing
```

---

## API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/health/deps` | – | Readiness: storage mode, Postgres, pgvector, DynamoDB, S3, LLM, worker |
| POST | `/api/v1/auth/register` | – | Create account |
| POST | `/api/v1/auth/login` | – | Obtain JWT |
| GET | `/api/v1/auth/me` | ✔ | Current user |
| POST | `/api/v1/meetings` | ✔ | Create meeting |
| GET | `/api/v1/meetings` | ✔ | List own meetings (paginated; `status`, `q` search) |
| GET | `/api/v1/meetings/{id}` | ✔ | Get one |
| PATCH | `/api/v1/meetings/{id}` | ✔ | Partial update |
| DELETE | `/api/v1/meetings/{id}` | ✔ | Delete (cascades to all derived data) |
| PUT | `/api/v1/meetings/{id}/transcript` | ✔ | Add or replace the transcript or meeting notes (`kind`: `transcript` / `notes`) |
| GET | `/api/v1/meetings/{id}/transcript` | ✔ | Get the transcript |
| POST | `/api/v1/meetings/{id}/process` | ✔ | Queue AI extraction → 202 + job (`?force=true` to re-run) |
| GET | `/api/v1/jobs/{job_id}` | ✔ | Poll a processing job |
| GET | `/api/v1/meetings/{id}/jobs` | ✔ | Processing history for a meeting |
| POST | `/api/v1/meetings/{id}/media/upload-url` | ✔ | Presigned direct upload for a recording |
| POST | `/api/v1/meetings/{id}/media/complete` | ✔ | Verify upload, queue transcription + processing |
| GET | `/api/v1/meetings/{id}/media` | ✔ | Recording metadata + playback URL |
| DELETE | `/api/v1/meetings/{id}/media` | ✔ | Delete the recording (transcript kept) |
| GET | `/api/v1/meetings/{id}/intelligence` | ✔ | Summary, participants, decisions, action items |
| GET | `/api/v1/meetings/{id}/summary` | ✔ | Summary with provenance and `is_stale` |
| GET | `/api/v1/meetings/{id}/decisions` | ✔ | Decisions |
| GET | `/api/v1/meetings/{id}/action-items` | ✔ | Action items |
| GET | `/api/v1/meetings/{id}/participants` | ✔ | Participants |
| GET | `/api/v1/action-items` | ✔ | All my action items (`status`, `overdue`, `meeting_id`) |
| PATCH | `/api/v1/action-items/{id}` | ✔ | Correct task / status / deadline / priority |
| PATCH | `/api/v1/decisions/{id}` | ✔ | Correct text / status |
| GET | `/api/v1/dashboard` | ✔ | Counts, recent meetings, action items needing attention |
| GET | `/api/v1/search` | ✔ | Semantic search over processed transcripts (`q`, `limit`, `meeting_id`) |
| GET | `/api/v1/meetings/{id}/mom` | ✔ | Structured Minutes of Meeting (409 `minutes_not_ready` before processing) |
| POST | `/api/v1/meetings/{id}/mom/pdf` | ✔ | Generate or reuse the MOM PDF; returns view and download links |
| POST | `/api/v1/ask` | ✔ | Answer a question from your meetings with cited sources (`question`, optional `meeting_ids`) |

Errors share one envelope:

```json
{"error": {"code": "not_found", "message": "Meeting not found.", "request_id": "..."}}
```

The `request_id` also appears on the `X-Request-ID` response header and in
every log line for that request.

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
| Job ends `FAILED` with `llm_rate_limited` | Gemini quota still exhausted after 3 attempts | Wait, then submit again; unchanged transcripts are served from cache |
| Upload POST to storage returns 400 | File exceeds `MEDIA_MAX_BYTES`, or type/key differs from the issued policy | Request a new upload URL matching the file |
| `complete` returns 422 `media_invalid` | File contents are not the declared audio/video format | Upload a genuine recording |
| `complete` returns 409 `manual_transcript_exists` | A typed transcript takes precedence | Resend with `replace_manual_transcript: true` |
| Job stays `QUEUED` forever | No worker running (`WORKER_EMBEDDED=false` without a standalone worker) | Start `python -m app.workers.processing`, or check `worker` in `/health/deps` |
| `/process` returns 409 `processing_in_progress` on transcript edit | A job is queued or running | Wait for the job to finish |
| Web app says "Cannot reach the MinuteAI server" | API not running on 8010 | Start uvicorn (step 5) |
| Job retries with `embedding_unavailable` | The embedding model could not be downloaded (offline, proxy) | Restore network access once; afterwards the cached model works offline |
| Search returns nothing for a meeting | The meeting has not been processed yet, or its transcript changed | Process it again (no second LLM call if results are current) |
| `/ask` returns 503 `llm_rate_limited` | Gemini quota exhausted | Wait for the quota to reset; unrelated questions still get an immediate "not found" |
| Ask says "not found" for a processed meeting | Minutes not indexed yet or the transcript changed | Process the meeting again (no extra LLM call if results are current) |
| Job fails with `no_audio_track` | The uploaded video has no sound | Upload a recording with audio, or add notes / a transcript |
| `/mom` returns 409 `minutes_not_ready` | The meeting has not been processed | Add content and process it |
| Tests fail on a fresh clone | `minuteai_test` missing | `docker compose down -v && docker compose up -d` (⚠️ destroys local data) |

# MinuteAI — AI Meeting Intelligence

**Cloud-based AI meeting intelligence and productivity system, built on polyglot
persistence and retrieval-augmented generation.**

MinuteAI turns any meeting into **structured Minutes of Meeting (MOM)** and a
**professional PDF**. Give it meeting notes, a transcript, an audio recording or
a video: it transcribes when needed, extracts what matters with Google Gemini,
checks the result against the source, and then lets you search everything you
have ever recorded and ask questions across it — with every answer traced back
to the meeting it came from.

| | |
|---|---|
| **Backend** | FastAPI · Python 3.12 · async SQLAlchemy 2.0 · Alembic |
| **Data** | PostgreSQL 16 + pgvector · DynamoDB · Amazon S3 |
| **AI** | Google Gemini · all-MiniLM-L6-v2 embeddings (local, ONNX Runtime) |
| **Frontend** | React 19 · TypeScript · Vite · TanStack Query |
| **Quality** | 298 backend tests · 62 frontend tests · ruff · oxlint · strict TypeScript |

---

## Table of contents

1. [The problem](#1-the-problem)
2. [The core idea](#2-the-core-idea)
3. [What the minutes contain](#3-what-the-minutes-contain)
4. [Features](#4-features)
5. [System architecture](#5-system-architecture)
6. [How a meeting is processed, step by step](#6-how-a-meeting-is-processed-step-by-step)
7. [Data model](#7-data-model)
8. [Semantic search and Ask your meetings](#8-semantic-search-and-ask-your-meetings)
9. [The web application](#9-the-web-application)
10. [Security and privacy](#10-security-and-privacy)
11. [Testing and quality](#11-testing-and-quality)
12. [Project structure](#12-project-structure)
13. [Getting started](#13-getting-started)
14. [API reference](#14-api-reference)
15. [Troubleshooting](#15-troubleshooting)
16. [Design decisions](#16-design-decisions)

---

## 1. The problem

Meetings produce decisions, commitments and deadlines, and then lose them. Notes
are written by whoever remembers to, recordings are never replayed, and a week
later nobody can say who owns what or what was actually agreed.

MinuteAI takes whatever a meeting leaves behind — a recording, a transcript, or
a few typed notes — and turns it into minutes that are structured, searchable
and verifiable, in about a minute, without anyone writing them by hand.

---

## 2. The core idea

```
 Meeting notes / description ─┐
 Transcript ──────────────────┤
 Audio ───────────────────────┼──►  Input processing
 Video ──► audio extraction ──┘             │
                                            ▼
                           Transcription with speaker labels
                                            ▼
                           Gemini structured extraction
                                            ▼
                           Validation: evidence, owners, deadlines
                                            ▼
                             Minutes of Meeting (MOM)
                  ┌─────────────────────────┼─────────────────────────┐
                  ▼                         ▼                         ▼
            Summary · keywords       Speakers · decisions      Action items
            discussion points        pending items             owner · deadline
                                            ▼
                    Professional PDF ──►  stored in S3  ──►  view / download
                                            ▼
              Embeddings (pgvector) ──► Semantic search ──► Ask your meetings
```

Everything after the input is automatic. The user chooses how to give MinuteAI
the meeting; the rest — transcription, extraction, validation, minutes, PDF,
indexing — happens in the background and is visible live in the web app.

---

## 3. What the minutes contain

Every generated MOM includes, wherever the information exists in the source:

| Section | Detail |
|---|---|
| Meeting title | As entered |
| Agenda / description | Shown on the minutes and given to the AI as context |
| Date and time | Used to resolve relative deadlines such as "next Friday" |
| Participants | Everyone named, plus everyone who demonstrably spoke |
| Speaker-wise contributions | One or two sentences per speaker, with speaking turns and share of words counted from the transcript |
| Executive summary | Three to six sentences |
| Key discussion points | The substance of the meeting |
| Keywords / topics | Three to ten topics, most important first |
| Decisions | What was agreed, with context and status |
| Action items | Task, **owner**, **deadline** (resolved date plus the original wording), priority, status |
| Pending / unresolved items | Open questions, deferred topics, work nobody owns |
| Next steps | What happens after the meeting |
| Source reference | Input type, transcription and extraction models, prompt version, transcript hash, and how many items were verified against the source |

Every decision, action item and pending item carries an **evidence quote** taken
from the source. The system checks each quote actually appears in the input and
marks it verified or unverified, so nothing in the minutes is untraceable.

---

## 4. Features

**Four ways to give MinuteAI a meeting**

- **Meeting notes** — typed notes or a written description. The AI is told it is
  reading notes, not speech.
- **Transcript** — pasted from Zoom, Teams or Meet.
- **Audio** — MP3, WAV, M4A, AAC, OGG, FLAC, WebM.
- **Video** — MP4, MOV, WebM. The audio track is extracted locally, so only
  speech is sent for transcription and the video itself never leaves the system.

Uploads go straight from the browser to object storage through a presigned POST
policy that pins the exact key, content type and size limit. After upload, the
file's real signature is checked against its declared type; a disguised file is
deleted rather than processed.

**Understanding the meeting**

- Transcription with speaker names and exact durations; the raw provider output
  is archived alongside the working transcript.
- One schema-constrained Gemini call produces the whole MOM.
- Deterministic post-processing resolves relative deadlines against the meeting
  date, links owners to participants (and refuses to guess when a first name is
  ambiguous), de-duplicates keywords, and verifies every evidence quote.
- Rule-based review flags surface what a careful minute-taker would check:
  action items with no owner or no deadline, deadline wording that could not be
  resolved, quotes not found in the source, unnamed speakers, and minutes whose
  transcript changed afterwards.

**Delivery**

- A4 PDF with a cover block, numbered sections, an action-item table, an evidence
  appendix and page numbers, in embedded Unicode fonts.
- Stored in S3 under the meeting's own prefix, addressed by a hash of its
  content: identical minutes reuse the stored file, changed minutes replace it.
- Viewed in the app or downloaded, through short-lived signed links.

**Finding things again**

- **Semantic search** over transcript passages: "signed out" finds "logged out".
- **Ask your meetings**: questions answered from your transcripts *and* minutes,
  with numbered citations, or an honest "not found" when the answer is not there.

**Operations**

- Background processing on a DynamoDB job queue with leases, heartbeats,
  fencing, exponential back-off and crash recovery.
- Live progress in the web app, driven by real job events.
- Structured JSON logs with a request id on every line and secret redaction.
- `/health` and `/health/deps` report every dependency individually.

---

## 5. System architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  React SPA — Vite · TypeScript · TanStack Query                            │
│  Dashboard · Meetings · Minutes · Action items · Search · Ask              │
└───────────────┬────────────────────────────────────────┬───────────────────┘
                │ HTTPS + JWT                            │ presigned POST
┌───────────────▼────────────────────────────────────┐   │  (uploads bypass
│  FastAPI                                           │   │   the API entirely)
│  api/v1   auth · meetings · intelligence · media · │   │
│           jobs · mom · search · ask · dashboard    │   │
│  services authorization · intelligence · grounding │   │
│           transcription · audio · mom · embeddings │   │
│           rag · job_store · storage · llm          │   │
│  worker   claim → transcribe → index → extract →   │   │
│           index minutes → PDF                      │   │
└──┬──────────────┬───────────────┬──────────────┬───┘   │
   │ SQLAlchemy   │ boto3         │ HTTPS        │ boto3 │
┌──▼───────────┐ ┌▼────────────┐ ┌▼───────────┐ ┌▼───────▼───────────┐
│ PostgreSQL16 │ │ DynamoDB    │ │ Google     │ │ Amazon S3          │
│ + pgvector   │ │ job queue,  │ │ Gemini     │ │ recordings, raw    │
│ source of    │ │ events,     │ │ transcribe │ │ transcripts,       │
│ truth + HNSW │ │ leases, TTL │ │ + extract  │ │ minutes PDFs       │
└──────────────┘ └─────────────┘ └────────────┘ └────────────────────┘
```

**Polyglot persistence — each store does one job**

| Store | Holds | Why this store |
|---|---|---|
| **PostgreSQL 16 + pgvector** | Users, meetings, transcripts, minutes, decisions, action items, participants, and the embedding vectors | Relational integrity and transactions for the source of truth; vectors live in the same database, so access control and similarity search happen in one query instead of two systems that can disagree |
| **DynamoDB** | Processing jobs, their step events, and per-meeting locks | Schema-flexible workflow state with conditional writes for exactly-once claiming, and TTL that expires operational history without a cleanup job |
| **Amazon S3** | Recordings, archived raw transcription output, generated PDFs | Purpose-built for large files; presigned URLs let uploads and downloads bypass the API process |

The whole stack runs locally through Docker Compose: PostgreSQL with pgvector,
DynamoDB Local, and an S3-compatible object store.

---

## 6. How a meeting is processed, step by step

1. **Create the meeting.** Title, date and time, optional agenda, and the input.
2. **Store the input.** Text is saved with its kind (notes or transcript). A
   recording is uploaded straight to object storage, then verified: size, content
   type, and real file signature. Only a verified upload gets a database row.
3. **Queue the work.** One transaction writes the job and a per-meeting lock, so
   submitting twice returns the same job instead of processing a meeting twice.
   The API answers in about 80 ms; nothing waits for the AI.
4. **Claim it.** A worker claims the job with a conditional update — exactly one
   winner — takes a lease, and renews it by heartbeat while it works.
5. **Extract audio** if the input is a video: 16 kHz mono Opus, locally.
6. **Transcribe** with speaker labels, when there is a recording. The transcript
   is pinned to the exact stored object, so the same recording is never
   transcribed twice, and a replaced recording is transcribed again.
7. **Index the transcript** into passages and vectors. This happens before the
   AI call, so a meeting is searchable even if the provider is busy.
8. **Extract the minutes** in one structured Gemini call, then normalise and
   verify the result deterministically and store everything in one transaction.
9. **Index the minutes** — summary, each decision, each action item, pending
   items and next steps — so questions can retrieve them directly.
10. **Render the PDF** and store it.

Every stage records an event on the job, and the web app shows them as they
happen: queued → transcribing → analysing → ready. A transient provider failure
is retried with back-off (30 s, then 2 min) without repeating work that already
succeeded; a permanent one fails fast with a clear reason. If a worker dies
mid-job, its lease lapses and another worker recovers the job.

---

## 7. Data model

```
users 1──N meetings 1──1 transcripts
                 ├──1 summaries            (+ keywords, speakers, pending items, next steps)
                 ├──N meeting_participants ──0..1 users
                 ├──N decisions
                 ├──N action_items ──0..1 meeting_participants
                 ├──1 meeting_media        (the uploaded recording)
                 └──N meeting_chunks       (vector(384) + provenance)
```

| Table | Purpose |
|---|---|
| `users` | Accounts; Argon2id password hashes |
| `meetings` | Title, agenda, date, source type, processing status |
| `transcripts` | One per meeting: text, SHA-256, counts, language, and provenance when transcribed |
| `meeting_media` | The verified recording: key, type, size, etag, original filename |
| `summaries` | Executive summary, key points, keywords, speaker contributions, pending items, next steps, and full provenance (provider, model, prompt version, transcript hash, tokens, latency) |
| `decisions` | Decision text, context, evidence quote and whether it was verified, status |
| `action_items` | Task, owner name and link, deadline plus original wording, priority, status, evidence |
| `meeting_participants` | Display name, normalised key, optional link to a user |
| `meeting_chunks` | Transcript and minutes passages with their 384-dimension vectors, an HNSW cosine index, and the transcript hash, model and chunker version they were built from |

Enumerations are `VARCHAR` with database `CHECK` constraints, so invalid values
are rejected by PostgreSQL itself. Deleting a meeting cascades to everything
derived from it, including its vectors and its stored files.

---

## 8. Semantic search and Ask your meetings

**Embeddings run locally.** all-MiniLM-L6-v2 is executed with ONNX Runtime at a
pinned model revision — no PyTorch, no external embedding API, and meeting text
never leaves the machine to be embedded. The vectors match the reference
sentence-transformers implementation to within 6.4 × 10⁻⁷.

**Chunking follows the conversation.** Transcripts are split at speaker turns and
packed into passages of at most 160 model tokens with overlap, so an exchange
that straddles a boundary is still retrievable. Each passage is an exact slice of
the transcript, which is what lets a search result open the transcript at the
right place. The minutes are indexed too: the summary, each decision, each action
item, each pending item and the next steps.

**Search** ranks passages by cosine similarity inside PostgreSQL. Ownership is
part of the same SQL statement, and pgvector's iterative index scans keep results
correct when a filter removes most candidates.

**Ask your meetings** adds retrieval-augmented generation on top:

```
question ─► embed ─► vector search across transcripts + minutes (owner-filtered)
         ─► relevance gate · at most 4 passages per meeting · top 8
         ─► context built from the live records (current owner, deadline, status)
         ─► Gemini answers with numbered [n] citations
         ─► citations verified in code ─► answer + only the cited sources
```

Three safeguards keep answers honest, and two of them are ordinary code rather
than model behaviour:

1. If nothing retrieved is related enough, the answer is "not found in your
   meetings" and the model is never called.
2. Citations that do not name a retrieved source are stripped out.
3. An answer that ends up citing nothing is reported as *not found*, never as an
   answer.

Each source shown with an answer carries its meeting, date and exact text, and
links to the highlighted transcript passage or to the meeting's minutes.

---

## 9. The web application

| Page | What it does |
|---|---|
| **Dashboard** | Greeting, counts by status, items needing attention, recent meetings; refreshes itself while anything is processing |
| **Meetings** | Search by title or description, filter by status, paginate |
| **New meeting** | Choose notes, transcript, audio or video; drag-and-drop with client-side checks and an upload progress bar |
| **Meeting** | Opens on the **Minutes**: summary, keywords, discussion points, decisions, action items with one-click status, pending items, next steps, speakers with share bars, review flags, source panel, and the PDF card. Tabs for action items, decisions and the full transcript |
| **Action items** | Everything across meetings, grouped as overdue, today, this week, later, no deadline and closed |
| **Search** | Semantic search (Ctrl K) with match strength; results open the transcript at the passage |
| **Ask your meetings** | A question thread; answers show citation chips that jump to their sources |

The interface is a hand-written design system: light and dark themes applied
before first paint, accessible dialogs and labels, and an off-canvas sidebar on
small screens. API types are generated from the backend's OpenAPI schema, so a
server change that breaks the UI fails the TypeScript build.

---

## 10. Security and privacy

- **Passwords** hashed with Argon2id; login and registration give identical
  answers for unknown and known accounts, so neither reveals whether an email
  exists.
- **Sessions** are short-lived JWTs, checked against the database on every
  request so a disabled account stops working immediately.
- **Authorization** goes through one function. A meeting that belongs to someone
  else returns **404**, not 403, so ids cannot be probed. The same rule is a JOIN
  in list endpoints and inside the vector search, so retrieval cannot leak across
  users.
- **Uploads** are constrained by the storage policy itself, validated by file
  signature, and never named from user input.
- **Prompt injection** is defended in layers: untrusted text is fenced, fence
  markers inside it are neutralised, the model is told the content is data, the
  output is schema-constrained, and nothing the model returns is executed.
- **Secrets** live in `.env` (git-ignored), are typed as secrets in code, and are
  redacted from logs. Meeting text is never logged.
- **Local development cannot reach real cloud accounts.** `STORAGE_BACKEND=local`
  refuses to start unless storage endpoints are local, builds AWS clients in an
  isolated session that ignores `~/.aws` and `AWS_PROFILE`, and blocks any
  outgoing request to a non-local host.

---

## 11. Testing and quality

```bash
pytest                    # from backend/  — 298 tests
npm test                  # from frontend/ —  62 tests
```

Tests run against **real infrastructure**, not mocks of it: a dedicated
PostgreSQL database with the real migrations, DynamoDB Local, and the real
S3-compatible server. Only the AI provider is a test double, and it satisfies the
same interface as the real one.

| Layer | What is covered |
|---|---|
| Pure logic | Deadline parsing, owner matching, evidence grounding, chunking, citation verification |
| Database | Concurrency (one job per meeting, one winner per claim), leases, fencing, crash recovery, cascade deletes, raw-SQL constraint checks |
| Storage | Presigned policies probed by sending violating uploads; signature sniffing for nine container formats |
| Media | Real MP4 files built in the test suite: audio extraction, exact durations, video without sound |
| Minutes and PDF | PDFs downloaded through their signed links and read back: sections, escaping, fonts, pagination, reuse, cleanup |
| Retrieval and answers | Attribution, grounding, isolation between users, scoping, relevance gate |
| Web app | The real routes rendered against a mocked API, asserted through roles and labels |
| Live (opt-in) | Real Gemini calls: extraction, transcription, and grounded answers |

Safeguards are **mutation-checked**: each one is deliberately broken to confirm a
test fails. Removing the ownership filter, the citation check, the relevance
gate, PDF escaping or the storage guard all break the suite.

Static analysis: `ruff` (lint + format) on the backend, `oxlint` and strict
TypeScript on the frontend, and `alembic check` to prove models and migrations
agree.

---

## 12. Project structure

```
.
├─ docker-compose.yml          PostgreSQL + pgvector, DynamoDB Local, S3-compatible storage
├─ .env.example                one configuration file for Compose and the backend
├─ backend/
│  ├─ app/
│  │  ├─ main.py               application factory, lifespan, embedded worker
│  │  ├─ api/v1/               auth · meetings · intelligence · media · jobs · mom · search · ask · dashboard
│  │  ├─ core/                 config, security, logging, middleware, AWS client guard
│  │  ├─ db/                   models and Alembic migrations
│  │  ├─ schemas/              request/response models and the LLM contracts
│  │  ├─ services/
│  │  │  ├─ llm/               provider interface + Gemini implementation
│  │  │  ├─ intelligence.py    extraction pipeline and normalisation
│  │  │  ├─ grounding.py       evidence verification
│  │  │  ├─ transcription.py   recording to transcript
│  │  │  ├─ audio.py           audio extraction from video
│  │  │  ├─ mom/               minutes builder, review flags, PDF renderer, PDF storage
│  │  │  ├─ embeddings/        chunking, local model, indexing, vector search
│  │  │  ├─ rag.py             Ask your meetings
│  │  │  ├─ job_store.py       DynamoDB job queue
│  │  │  ├─ storage.py         S3 access and presigned URLs
│  │  │  └─ authorization.py   the single access rule
│  │  ├─ workers/              background processing worker
│  │  └─ evaluation/           offline retrieval evaluation
│  └─ tests/                   unit, integration and opt-in live tests
├─ frontend/
│  └─ src/
│     ├─ api/                  typed client (generated from OpenAPI), uploads
│     ├─ auth/                 session and auth context
│     ├─ components/           design system, minutes view, PDF preview, processing stepper
│     ├─ lib/                  pure helpers (formatting, citations, grouping, theme)
│     └─ pages/                dashboard, meetings, meeting, action items, search, ask
├─ docs/
│  ├─ architecture.md          full system design
│  ├─ PROJECT_STATUS.md        what each part delivers, and how it was verified
│  └─ adr/                     architecture decision records
├─ infra/                      container initialisation scripts
└─ scripts/                    sample-audio generation
```

---

## 13. Getting started

### Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.12** | Not 3.13/3.14 — some media and ML dependencies lack wheels for those. `winget install --id Python.Python.3.12 --exact` |
| **Docker Desktop** | Must be running before `docker compose up` |
| **Node.js 20+** | For the web app (tested with npm 11) |
| **Git** | |

Ports **5432**, **8001**, **8010**, **9000**, and **5173** must be free.

> **Windows note:** if Anaconda is installed, `python` on your PATH is probably
> Anaconda's. Always create the venv with `py -3.12`, never `python -m venv`.

### Installation

#### 1. Clone and configure

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

#### 2. Start the databases

```bash
docker compose up -d
```

Wait for Postgres to report healthy:

```bash
docker compose ps
```

#### 3. Create the Python environment

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

#### 4. Apply migrations

```bash
alembic -c backend/alembic.ini upgrade head
```

#### 5. Run the API

```bash
uvicorn app.main:app --reload --port 8010 --app-dir backend
```

Open <http://localhost:8010/docs>.

#### 6. Run the web app

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

### Verify the installation

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

### Using the API directly

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

#### Uploading a recording

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

#### Running the worker separately

By default the worker runs inside the API process. To run it as its own process
(for example, to scale it independently), set `WORKER_EMBEDDED=false` and start:

```bash
python -m app.workers.processing
```

---

## 14. API reference

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

## 15. Troubleshooting

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

---

## 16. Design decisions

Every significant choice is recorded as an architecture decision record in
[docs/adr](docs/adr), with the alternatives that were rejected and why:

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-polyglot-persistence.md) | Polyglot persistence: PostgreSQL + DynamoDB + S3, each for one job |
| [0002](docs/adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector instead of a dedicated vector database |
| [0003](docs/adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](docs/adr/0004-ownership-only-authorization-in-m1.md) | One central authorization rule, 404 instead of 403 |
| [0005](docs/adr/0005-argon2-over-bcrypt.md) | Argon2id password hashing |
| [0006](docs/adr/0006-gemini-as-initial-llm-provider.md) | Google Gemini behind a provider-neutral interface |
| [0007](docs/adr/0007-structured-extraction-with-deterministic-validation.md) | Structured extraction with deterministic validation and evidence grounding |
| [0008](docs/adr/0008-dynamodb-job-queue-with-leased-workers.md) | DynamoDB job queue with leased workers |
| [0009](docs/adr/0009-recording-upload-and-transcription.md) | Presigned-POST uploads, signature validation, Gemini transcription |
| [0010](docs/adr/0010-explicit-storage-backend.md) | Explicit storage backend so local development cannot reach real AWS |
| [0011](docs/adr/0011-react-frontend.md) | React SPA with API types generated from OpenAPI |
| [0012](docs/adr/0012-local-embeddings-and-semantic-search.md) | Local embeddings via ONNX Runtime, turn-based chunking, filtered HNSW search |
| [0013](docs/adr/0013-minutes-of-meeting-and-pdf.md) | One Minutes-of-Meeting model, review flags, PDF stored by content hash |
| [0014](docs/adr/0014-ask-your-meetings-rag.md) | RAG over transcripts and minutes, with citation verification in code |

Further reading: [docs/architecture.md](docs/architecture.md) for the full system
design, and [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for what each part
delivers and how it was verified.

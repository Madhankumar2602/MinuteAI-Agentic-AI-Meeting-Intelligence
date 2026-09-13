# MinuteAI — Project Status

**Last updated:** 2026-09-13
**Current milestone:** M4 — Recordings + S3 + transcription ✅ **COMPLETE**
**In progress:** M5 — React frontend

---

## Milestone progress

| # | Milestone | Status |
|---|---|---|
| M1 | Foundation — Docker, Postgres+pgvector, DynamoDB Local, FastAPI, auth, meeting CRUD | ✅ `v0.1.0` |
| M2 | Text-first AI intelligence — summary, decisions, action items | ✅ `v0.2.0` |
| M3 | Async processing + DynamoDB job state | ✅ `v0.3.0` |
| **M4** | Recordings + S3 + transcription | ✅ `v0.4.0` (S3 local; real AWS S3 in M10) |
| M5 | React frontend | 🔄 In progress |
| M6 | Embeddings + pgvector | ⬜ Not started |
| M7 | Cross-meeting RAG | ⬜ Not started |
| M8 | Agent automation | ⬜ Not started |
| M9 | Agent UI + human approval | ⬜ Not started |
| M10 | AWS deployment | 🔴 Needs AWS account |
| M11 | Lambda + EventBridge | 🔴 Needs AWS account |
| M12 | Testing + evaluation + finalisation | ⬜ Not started |

---

## M4 — completed features (ADR 0009)

- [x] S3-compatible storage locally (RustFS, pinned) after MinIO was withdrawn and LocalStack began requiring a licence token; 12-point compatibility probe
- [x] Presigned **POST** uploads: storage itself enforces key, content type, and size (verified by sending violating uploads)
- [x] Typed, short-lived upload tokens bound to user + meeting; no DB row until an upload is verified
- [x] File-signature sniffing for WAV, MP3, AAC, OGG, FLAC, WebM, MP4/M4A, MOV; disguised files deleted (mutation-checked)
- [x] Filenames sanitised and never used in keys or paths
- [x] `meeting_media` table; transcript provenance (media id, etag, raw S3 key, model, duration) — migration `0004`
- [x] Transcription stage inside the existing job: events, retries, leases, and fencing all apply
- [x] Etag rule: typed transcript wins; same recording never transcribed twice; replaced recording re-transcribed
- [x] Gemini Files API transcription (`gemini-3.6-flash`); remote copy deleted after every call
- [x] Raw structured transcription archived to S3; exact WAV duration from the header (model timestamps unreliable)
- [x] Recording playback via short-lived presigned GET; recording delete keeps the transcript
- [x] Meeting deletion removes its S3 objects; `/health/deps` checks the bucket
- [x] Synthetic meeting audio generated independently with Windows TTS (`scripts/generate_sample_audio.ps1`)

**Verified live:** 4.9 MB upload in 0.72 s direct to storage; transcription 23–31 s (WER 2.7%);
upload-to-results 47 s; extraction on transcribed text fully correct; playback URL works.

**Found while building M4:**
- `minio/minio` no longer exists on Docker Hub; `localstack/localstack` exits without a licence token
- Git Bash rewrites `/data` to `C:/Program Files/Git/data` in `docker run` arguments (use Compose, or `MSYS_NO_PATHCONV=1`)
- `gemini-3.5-transcribe` does not support JSON output
- Transcription misattributed two speaker turns; timestamps ran past the recording's end; "Meera" was once spelled "Mira"

**Tests:** 159 passed; 3 opt-in live tests (health, extraction, transcription) pass.

---

## M3 — completed features

### Job queue in DynamoDB (ADR 0008)
- [x] Table `minuteai_processing_jobs`: job items + per-meeting lock items, `gsi_meeting`, `gsi_status`, TTL 30 days
- [x] Created idempotently at start-up (`DYNAMODB_AUTO_CREATE_TABLES`, off in AWS)
- [x] Submission is one transaction: job + lock, both conditional → **at most one active job per meeting**
- [x] Duplicate or concurrent submissions return the existing active job (tested with 5 concurrent submits)
- [x] Claim by conditional update → **exactly one winner** (tested with 5 concurrent claimers)
- [x] FIFO claiming; retry back-off honoured via `available_at`
- [x] Leases with heartbeat renewal; **fencing**: stale workers cannot overwrite outcomes
- [x] Recovery of jobs abandoned by crashed workers (requeue, or fail when attempts are exhausted)
- [x] Terminal transition + lock release in one transaction
- [x] Per-job event timeline: `queued → started → retry_scheduled / lease_expired_requeued → completed / failed`
- [x] Strongly consistent job polling (base-table `GetItem`)

### Worker
- [x] `ProcessingWorker`: runs embedded in the API (default) or standalone (`python -m app.workers.processing`)
- [x] Bounded concurrency (`WORKER_CONCURRENCY`), wake-on-submit, 2 s polling, 10 s drain on shutdown
- [x] Error classification: transient errors retried at 30 s × 4^(n−1) up to 3 attempts; permanent errors fail at once
- [x] Safe error messages only; raw exception text stays in logs
- [x] Crash after results were committed → recovered job completes from cache, no second LLM call
- [x] Worker health in `/health/deps` when embedded

### API
- [x] `POST /api/v1/meetings/{id}/process` → **202** with job (median **83 ms**, was 19.5 s) or **200** `{cached: true}`
- [x] `GET  /api/v1/jobs/{job_id}` — authorised through the job's meeting
- [x] `GET  /api/v1/meetings/{id}/jobs` — history, newest first
- [x] New meeting status `queued`; transcript edits refused while queued or processing
- [x] `/health/deps` checks the jobs table is `ACTIVE`, not just that DynamoDB answers

### Pipeline refactor
- [x] `process_meeting` split into `require_transcript`, `is_result_current`, `run_extraction`, `set_meeting_status`
- [x] The worker, not the pipeline, decides `queued` vs `failed`, because only the worker knows whether it will retry

---

## Defects found and fixed during M3

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | **Enum CHECK constraints never existed.** Docs and model comments claimed `VARCHAR + CHECK`; PostgreSQL accepted `status = 'nonsense'` | Checked the live schema before adding a status | `create_constraint=True` on all enums; migration `0003` adds 6 constraints; raw-SQL regression tests |
| 2 | **Crash-recovery cache check could never succeed.** `is_result_current` required status `completed`, but recovery had just set it to `queued` | New recovery test failed | Currency decided from atomically-stored provenance alone; status restored on cache hit |
| 3 | **Production-only 500 on every `/process`.** `extra={"created": …}` is a reserved `LogRecord` key | Live run through uvicorn | Key renamed; static test rejects reserved keys |
| 4 | **All app logging silently disabled in tests since M1.** Alembic's `fileConfig` defaults to `disable_existing_loggers=True` | Investigating why #3 escaped the suite | `disable_existing_loggers=False`; tests assert loggers are enabled and run at DEBUG; proven by reintroducing #3 |
| 5 | DynamoDB `ValidationException` reported as "temporarily unavailable" (503) | `result` reserved-word error during store smoke test | Only throttling/service errors map to 503; attribute-name placeholders in expressions |

---

## Test status

```
111 passed, 2 skipped (live, opt-in) in ~59s     ruff: clean     alembic check: no drift
live Gemini tests: 2 passed

tests/test_jobs.py              16  submission, worker success/retry/back-off/max attempts,
                                    permanent failure, resubmission, deleted meeting,
                                    crash recovery (x2), run_forever loop, authorization
tests/test_meetings.py          16  CRUD, pagination, validation, isolation
tests/test_normalisation.py     16  grounding, deadlines, owner matching, normalisation
tests/test_job_store.py         12  real DynamoDB Local: concurrency, FIFO, locks, leases, fencing, TTL
tests/test_processing.py        12  transcripts, extraction results, cache, force, staleness, isolation
tests/test_gemini_provider.py   11  retry policy and error translation
tests/test_auth.py               9  registration, login, tokens, enumeration guards
tests/test_action_items.py       8  cross-meeting list, overdue, corrections, isolation, cascade
tests/test_health.py             6  liveness, readiness, request id, error envelope
tests/test_schema_constraints.py 4  database rejects invalid enum values (raw SQL)
tests/test_logging_hygiene.py    1  no reserved LogRecord keys in any log call
tests/live/test_gemini_live.py   2  real API (opt-in)
```

---

## Verified live (real processes, DynamoDB Local, Gemini)

| Scenario | Result |
|---|---|
| Submit | `202` in 37–108 ms (median 83 ms) warm; first request after start 1.4 s (cold clients) |
| **Worker hard-killed mid-Gemini call** | Lease expired 20.5 s after start (20 s lease); worker #2 recovered it, completed on attempt 2 with 2 decisions / 3 action items; single job record |
| Job left by an earlier killed worker | Recovered (`stale job recovered`) and completed by the next worker |
| Standalone worker + API without embedded worker | Works |
| Embedded worker, 3 simultaneous jobs | Max 2 concurrent extractions; all completed in 13–30 s |
| Re-submit unchanged meeting | `200 cached=true` in 15 ms |
| `/health/deps` | postgres, dynamodb (jobs table ACTIVE), gemini, worker all healthy |

---

## Known issues / limitations

| Item | Severity | Plan |
|---|---|---|
| Job outcome (DynamoDB) and meeting status (PostgreSQL) are not one transaction — a crash between them can leave them disagreeing | Low | Job is authoritative; resubmission corrects it. Documented in ADR 0008 |
| LLM calls are at-least-once: a crash mid-call spends quota again on recovery | Low | Results replaced idempotently |
| Standalone worker may start a job up to 2 s late (polling) | Low | Embedded worker is woken instantly |
| Graceful shutdown drains for only 10 s; longer jobs are recovered after lease expiry | Low | By design |
| Graceful shutdown drain not exercised live (Windows cannot send Ctrl+C to a background process); covered by the `run_forever` stop test | Low | Re-verify in M10 on Linux |
| Re-processing resets manual status changes on action items | Low | ADR 0007; `force=true` required |
| Evidence verification proves a quote exists, not that it supports the claim | Low | Measure in M12 |
| No rate limiting on `/auth/login` or `/process` | Medium | Before public deployment (M10) |
| Free-tier Gemini content may be used by Google | Medium | Synthetic/consented transcripts only |
| pgvector on RDS not yet verified | Medium | Before M10 |

---

## Technical decisions

| ADR | Decision |
|---|---|
| [0001](adr/0001-polyglot-persistence.md) | Polyglot persistence — PostgreSQL + DynamoDB + S3 |
| [0002](adr/0002-pgvector-over-dedicated-vector-db.md) | pgvector over a dedicated vector database |
| [0003](adr/0003-async-sqlalchemy.md) | Async SQLAlchemy with asyncpg |
| [0004](adr/0004-ownership-only-authorization-in-m1.md) | Ownership-only authorization |
| [0005](adr/0005-argon2-over-bcrypt.md) | Argon2id via argon2-cffi |
| [0006](adr/0006-gemini-as-initial-llm-provider.md) | Google Gemini as the initial LLM provider |
| [0007](adr/0007-structured-extraction-with-deterministic-validation.md) | Structured extraction with deterministic post-processing |
| [0008](adr/0008-dynamodb-job-queue-with-leased-workers.md) | DynamoDB job queue with leased workers (refines the review's key design) |

---

## Environment

- **Repository:** `C:\Users\madhan\OneDrive\Desktop\adv sql` (snapshot of M1 at `C:\dev\minuteai-BACKUP-2026-09-12`)
- **Note:** inside OneDrive. If `pip install` fails with a file-lock error, pause OneDrive sync.
- **Python:** 3.12.10 in `backend/.venv` · **Ports:** Postgres 5432 · DynamoDB Local 8001 · API 8010
- **LLM:** `gemini-3.6-flash` · **Jobs table (dev):** `minuteai_processing_jobs`

## Deployment status

Local development only. No cloud resources provisioned.

---

## Next

**M4 (audio + S3 + transcription)** is the next milestone in order. It needs an
AWS account for S3. Everything else in M4 can be built first: the upload flow,
audio validation, Gemini transcription (the key already works), the `transcribe`
pipeline step, and an S3 storage interface. Only the final live S3 wiring waits
on credentials.

**M5 (frontend)** and **M6 (embeddings + pgvector)** need no credentials at all
and could be done first.

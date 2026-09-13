# ADR 0008 — Background processing on a DynamoDB job queue with leased workers

- **Status:** Accepted
- **Date:** 2026-09-13
- **Milestone:** M3

## Context

In M2, `POST /meetings/{id}/process` held the HTTP request open for the whole
LLM call: 5–30 s per request, measured at 19.5 s live. That design has four
concrete problems:

1. Clients and proxies time out; browsers make users wait with no progress.
2. A client disconnect mid-call could leave a meeting stuck in `processing`.
3. Nothing retried transient provider failures beyond the LLM client's short
   in-request retries.
4. There was no durable record of what was attempted, when, and why it failed.

M3 must return immediately, track `QUEUED → PROCESSING → COMPLETED / FAILED`,
make failures visible and recoverable, and be retry-safe.

## Decision

A **job queue stored in DynamoDB**, consumed by a **worker that holds a
time-limited lease** on each job.

### Submission

`POST /process` authorises the request and validates what can be validated
synchronously (a missing transcript is still an immediate 409). It then writes
one DynamoDB transaction: the job item (`QUEUED`) plus a per-meeting lock item,
both conditional on not already existing. It responds `202 Accepted` with the
job. Measured: median 83 ms.

- If the lock already exists, the meeting's active job is returned instead.
  Double-clicks and client retries cannot start duplicate LLM runs.
- If stored results are already current (ADR 0007), the API answers
  `200 {"cached": true, "job": null}` and queues nothing.

### Claiming and leasing

A worker queries the status index for the oldest `QUEUED` job whose
`available_at` has passed. It takes ownership with a **conditional update**
(`job_status = QUEUED AND available_at = <value it read>`), which succeeds for
exactly one worker however many race for the job.

The claim sets a lease (`available_at = now + JOB_LEASE_SECONDS`). A heartbeat
renews it every lease/3 while the job runs, conditional on the worker still
owning the job.

### Outcomes

| Outcome | Job | Meeting (PostgreSQL) | Lock |
|---|---|---|---|
| Success | `COMPLETED`, result counts stored | `completed` | released |
| Transient error (429, 5xx, DB/job-store outage), attempts remain | `QUEUED`, `available_at = now + 30 s × 4^(n−1)` | `queued` | kept |
| Permanent error (bad key, invalid output, deleted meeting, bug), or attempts exhausted | `FAILED`, error code + safe message | `failed` | released |

Terminal transitions and lock release happen in **one transaction**, conditional
on ownership.

### Crash recovery and fencing

If a worker dies, its lease stops being renewed. Any worker, on its next poll,
finds `PROCESSING` jobs whose `available_at` (the lease expiry) has passed. It
requeues them if attempts remain, otherwise fails them.

Every write a worker makes about a job's outcome is conditional on
`worker_id = me`. A worker that was presumed dead but was only slow is **fenced
out**: its late "completed" write is rejected rather than overwriting the newer
attempt. This was tested, and verified live by hard-killing a worker mid-Gemini
call. Recovery happened 20.5 s later with a 20 s lease, and the job completed
on attempt 2 with correct results and no duplicates.

A crash *after* results were committed but *before* the job was marked complete
is detected on recovery: the results are current, so the job is completed
without a second LLM call.

### Deployment shape

The worker runs **inside the API process by default** (`WORKER_EMBEDDED=true`),
started and drained by the application lifespan. The same code runs standalone
with `python -m app.workers.processing`. Both modes were verified live.
Concurrency per worker is bounded (`WORKER_CONCURRENCY`, default 2).

## Table design

```
pk (partition key only)
  JOB#<job_id>             job item
  LOCK#MEETING#<meeting>   lock item (exists only while a job is active)

GSI gsi_meeting   meeting_pk + created_at     job history per meeting
GSI gsi_status    job_status + available_at   work queue AND abandoned-lease scan
TTL expires_at    30 days                     operational data expires on its own
```

**This refines the key design sketched in the architecture review**, which put
`MEETING#<id>` in the partition key. The job id was chosen instead because the
hottest read is a client polling a single job. Only a base-table `GetItem` can
be strongly consistent; a GSI query cannot. So polling gets a guaranteed-fresh
answer, while per-meeting history (rarer) tolerates index lag.

`available_at` is deliberately overloaded — "runnable from" for queued jobs,
"lease expires at" for running ones — so one index range key serves both the
queue and the recovery scan.

## Alternatives considered

**FastAPI `BackgroundTasks` / a bare `asyncio.create_task`.** Minimal code. But
work lives only in memory: a restart or crash loses queued jobs silently, there
is no retry, no multi-process safety, and no durable status. It fails M3's
"visible and recoverable" requirement outright.

**Celery or RQ with Redis.** Mature and feature-rich. Rejected because it adds
a broker, a second process model, and a dependency explicitly discouraged for
this project, only to reimplement what M3 needs in ~600 readable lines. Its job
state would also live outside the project's chosen stores.

**Amazon SQS + a worker.** The natural choice at larger scale, and a sensible
future step. Locally it needs an emulator. SQS also provides delivery, not
*state*: a status table would still be needed for polling and history, and it
would be DynamoDB anyway.

**PostgreSQL as the queue (`SELECT … FOR UPDATE SKIP LOCKED`).** Stated
honestly: this is the strongest alternative and simpler in one important
respect. The job row could be updated in the same transaction as the results,
removing the cross-store consistency window described below. It was not chosen
because:
(a) the project's architecture deliberately separates ephemeral workflow state
from the relational source of truth (ADR 0001);
(b) M11's scheduled Lambda agent will record its runs in the same DynamoDB
design, reachable without a VPC connection to the database;
(c) DynamoDB TTL expires operational history without a cleanup job.
If the polyglot requirement did not exist, `SKIP LOCKED` would be the
recommendation.

## Consequences

**Advantages**
- Requests return in ~80 ms; the UI polls one strongly consistent `GetItem`.
- Survives worker crashes and restarts without losing or duplicating work.
- Safe with any number of API or worker processes, with no in-process
  coordination.
- Every attempt is auditable through the job's event timeline:
  `queued → started → retry_scheduled → started → completed`.
- Transient provider problems resolve themselves without user action.

**Limitations**
- **Two stores, no shared transaction.** The job outcome (DynamoDB) and the
  meeting status (PostgreSQL) are written one after the other. If the process
  dies between them, the job can say `FAILED` while the meeting still says
  `processing`. The job is the authoritative operational record; a new
  submission is accepted and resets the meeting correctly. Results are never
  partial: they commit in one PostgreSQL transaction.
- **Polling, not push.** Idle workers query DynamoDB every `WORKER_POLL_SECONDS`
  (2 s). In-process `notify()` removes the delay for the embedded worker; a
  standalone worker may pick up a job up to one poll interval late.
- **At-least-once LLM calls.** A crash mid-call means the call is made again on
  recovery. Results are replaced idempotently, but the provider quota is spent
  twice.
- **Lease sizing matters.** The lease must exceed the heartbeat interval with
  margin. The default is 120 s with a heartbeat every 40 s. A stalled event
  loop longer than the lease would let a job be taken over; fencing keeps that
  safe, but it wastes an attempt.
- **Graceful shutdown drain is bounded at 10 s**; longer jobs are cancelled and
  recovered after their lease expires.

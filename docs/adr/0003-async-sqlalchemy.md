# ADR 0003 — Asynchronous SQLAlchemy with asyncpg

- **Status:** Accepted
- **Date:** 2026-09-12
- **Milestone:** M1

## Context

FastAPI is an ASGI framework and supports both `async def` and `def` route
handlers (the latter run in a threadpool). SQLAlchemy 2.0 supports both a
synchronous and an asynchronous API. A choice was required before the driver
dependency could be pinned.

MinuteAI's later milestones are dominated by I/O that is *not* the database:
LLM API calls (seconds), transcription (tens of seconds), embedding requests,
and in M8 an agent loop that interleaves several tool calls with LLM calls.
While any of those waits, the process should be serving other requests.

## Decision

Use async SQLAlchemy 2.0 with the `asyncpg` driver. Every route handler,
dependency, and service function in the request path is `async def`.

## Alternatives considered

**Synchronous SQLAlchemy with psycopg.** Simpler, better documented, and far
easier to debug — there is no greenlet machinery to misunderstand. FastAPI
would run sync handlers in a threadpool, which works correctly.

Rejected because it produces a codebase with two execution models: `async def`
routes for the LLM-calling paths, `def` routes for the database-only paths, and
`run_in_threadpool` bridges wherever they meet. Every new function would start
with the question "which colour is this?". One model throughout is easier to
reason about, and easier to explain in a viva.

**Async with psycopg 3.** Viable — psycopg 3 has native async support. asyncpg
was preferred for its maturity in SQLAlchemy's async dialect and its
performance, and because it has prebuilt Windows wheels for Python 3.12.

## The cost, stated honestly

Async SQLAlchemy **forbids implicit lazy loading**. Accessing an unloaded
relationship outside an awaited context raises
`MissingGreenlet: greenlet_spawn has not been called`, which is an opaque error
for a newcomer.

Two measures turn this from a trap into a discipline:

1. Every relationship is declared `lazy="raise"`. An accidental lazy load then
   fails immediately with a clear SQLAlchemy message naming the relationship,
   rather than with a greenlet error from deep inside the async bridge.
2. Relationships must be loaded explicitly with `selectinload()`. This is the
   same practice that prevents N+1 query patterns, so the constraint improves
   the code rather than merely working around the runtime.

Additionally, `expire_on_commit=False` is set on the session factory. With the
default, every attribute read after a `commit()` triggers a refresh — which in
async code frequently happens while FastAPI is serialising the response, i.e.
outside the session context, producing the same greenlet error.

## Consequences

**Advantages**
- One execution model across the whole request path.
- The event loop stays free during the long LLM and transcription waits that
  dominate M2, M4, and M8.
- Eager loading is mandatory, so N+1 queries cannot creep in unnoticed.

**Limitations**
- Alembic needs the async template (`alembic init -t async`); its `env.py` runs
  migrations through `connection.run_sync()`.
- Any synchronous library used from a route — `boto3`, for example — must be
  dispatched with `run_in_threadpool`, or it blocks the loop. This is done in
  `app/services/dynamo.py`.
- Tests must use an async client (`httpx.ASGITransport`) rather than the
  synchronous `TestClient`.

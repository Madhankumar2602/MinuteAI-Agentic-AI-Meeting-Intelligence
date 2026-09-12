# ADR 0001 — Polyglot persistence: PostgreSQL + DynamoDB + S3

- **Status:** Accepted
- **Date:** 2026-09-12
- **Milestone:** M1

## Context

MinuteAI handles four kinds of data with genuinely different characteristics:

1. **Business records** — users, meetings, decisions, action items. Highly
   relational, queried with joins and filters, must be transactionally
   consistent (an action item without its meeting is meaningless).
2. **Vectors** — 384-dimensional embeddings of transcript chunks, searched by
   similarity, and always filtered by who is allowed to see them.
3. **Workflow state** — AI processing jobs and agent runs. Append-heavy, shape
   changes as the pipeline grows, valuable for days rather than years.
4. **Large binaries** — meeting audio and video, tens to hundreds of megabytes.

Storing all four in one database is possible but wrong in at least two places:
audio in a relational BLOB column bloats backups and slows every restore, and
job-event rows churning through the OLTP tables adds write pressure and
vacuum load to the tables that serve user requests.

## Decision

Use three stores, each chosen for one job:

| Store | Holds | Chosen because |
|---|---|---|
| **PostgreSQL 16** | users, meetings, participants, transcripts, summaries, decisions, action items, meeting chunks + embeddings | Relational integrity, foreign keys, joins, transactions; and via pgvector it also serves similarity search (see ADR 0002) |
| **DynamoDB** | processing jobs, step events, agent runs | Schema-flexible for a pipeline whose stages are still changing; native TTL expires operational data automatically; reachable from Lambda (M11) without VPC attachment |
| **Amazon S3** | audio, video, raw transcript files, generated exports | Purpose-built for large objects; presigned URLs let uploads bypass the API process entirely |

PostgreSQL is the **source of truth**. The other two hold data that is either
derivable (S3 objects are referenced by a key stored in Postgres) or
intentionally ephemeral (DynamoDB rows expire).

## Alternatives considered

**Everything in PostgreSQL.** Simplest to operate, and honestly defensible at
this scale. Rejected because storing multi-hundred-megabyte media in the
database makes backup and restore impractical, and because the project is
explicitly meant to demonstrate polyglot persistence. The job-state argument
alone would not have justified DynamoDB; the media argument does justify S3.

**MongoDB for everything.** Would handle jobs and documents well, but the core
domain is strongly relational and the ACL-filtered vector search in M7 depends
on joining chunks to meetings to permissions. Losing foreign keys to gain
schema flexibility in one subsystem is a bad trade.

**PostgreSQL + S3 only, jobs in a Postgres table.** A legitimate simplification,
and the fallback if DynamoDB proves troublesome. Rejected primarily because the
M11 scheduled Lambda would then need database access inside a VPC — the single
most common deployment failure in projects of this shape.

## Consequences

**Advantages**
- Each store is used for what it is good at, and each choice has a concrete
  technical reason rather than a résumé-driven one.
- The Lambda agent in M11 needs no VPC configuration.
- Media never travels through the API process.

**Limitations**
- Three stores mean three sets of credentials, three health checks, and three
  failure modes. `/health/deps` exists specifically to make that tractable.
- No cross-store transaction. If a job row is written and the following
  Postgres commit fails, the two can disagree. Mitigated by treating
  PostgreSQL as authoritative and DynamoDB as advisory: a stale job record is
  cosmetic, never a data-integrity problem.
- Local development requires Docker for two of the three.

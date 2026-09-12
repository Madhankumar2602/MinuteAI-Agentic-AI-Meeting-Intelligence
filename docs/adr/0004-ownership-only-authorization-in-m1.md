# ADR 0004 — Ownership-only authorization in M1, with sharing as a designed extension point

- **Status:** Accepted
- **Date:** 2026-09-12
- **Milestone:** M1

## Context

The architecture includes a `meeting_shares` table so a meeting can be shared
with other users (`viewer` / `editor`). No milestone through M4 uses sharing:
meetings are created, processed, and read by their owner.

Two project rules pull in opposite directions. One says keep the sharing model
in the architecture. Another says do not create placeholder structures for
future phases.

## Decision

Implement **ownership-only** authorization in M1, but route it through a single
function that is already shaped for sharing:

```python
async def authorize_meeting_access(
    db: AsyncSession,
    meeting_id: uuid.UUID,
    user: User,
    level: AccessLevel = AccessLevel.READ,
) -> Meeting
```

- The `meeting_shares` table is **not** created yet.
- `AccessLevel.READ` / `AccessLevel.WRITE` already exist, and every call site
  declares which it needs — so when sharing arrives, a `viewer` grant will not
  accidentally confer write access.
- Every route that names a specific meeting goes through this function. No
  route queries `meetings` by id directly. That invariant is what makes the
  guarantee auditable: one function to read, one function to change.

Adding sharing later is a migration plus a `LEFT JOIN` inside this one
function. No route changes.

## Security detail: 404 rather than 403

When a user requests a meeting they do not own, the response is **404 Not
Found**, identical to the response for a meeting id that does not exist.

Returning 403 would confirm that the id is real. An attacker holding any valid
account could then enumerate meeting ids and learn which exist by distinguishing
403 from 404. Both cases therefore return an identical body, status, and timing
profile.

The denial is logged at WARNING with the user id and meeting id — invisible to
the caller, but a clear signal in the logs if someone is probing.

## Alternatives considered

**Build `meeting_shares` now.** Rejected: an unused table with unused code paths
cannot be meaningfully tested, and untested authorization code is worse than
absent authorization code because it invites false confidence.

**Check ownership inline in each route.** Rejected: five routes today, perhaps
twenty by M9. Each inline check is an independent opportunity to forget one,
and a reviewer would have to read every route to verify the guarantee holds.

**Per-route FastAPI dependency.** Attractive, but the function needs the
meeting id from the path and must return the loaded object; a dependency would
either re-query or complicate the signature. Direct calls are clearer.

## Consequences

**Advantages**
- The security guarantee lives in one auditable place and is covered by tests
  that assert a second user gets 404 on read, update, and delete.
- No dead schema.
- Sharing remains a small, well-defined change.

**Limitations**
- Until sharing is built, `AccessLevel.READ` and `AccessLevel.WRITE` behave
  identically. This is deliberate — the distinction records intent at each call
  site now, so the later change is safe.
- Callers must remember to use the function. Enforced by code review and by the
  authorization tests, not by the type system.

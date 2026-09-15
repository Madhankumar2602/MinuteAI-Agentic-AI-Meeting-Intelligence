# ADR 0007 — Structured LLM extraction with deterministic post-processing

- **Status:** Accepted
- **Date:** 2026-09-13
- **Milestone:** M2

## Context

M2 turns a transcript into a summary, decisions, and action items with owners
and deadlines. An LLM is the only practical way to read meeting language, but
LLM output has well-known failure modes:

- malformed or incomplete JSON
- plausible content nobody said (a "high" priority that was never stated was
  observed in the first live test, before the prompt was tightened)
- relative dates resolved against the wrong reference, or the wrong year
- the same person under two spellings, or an owner missing from the
  participant list
- instructions embedded in the transcript itself (prompt injection)

The question is which of these to trust the model with, and how to catch the
rest.

## Decision

Split the work in two. The model does only what requires language
understanding; everything that can be computed is computed in Python.

```
LLM (Gemini)                        Deterministic Python
─────────────────────────────       ─────────────────────────────────────
read the transcript                 validate JSON against Pydantic schema
identify decisions vs proposals     trim / de-duplicate / drop blank items
identify tasks, owners, dates       parse YYYY-MM-DD, reject implausible dates
resolve "next Friday" → a date      match owner names to participants
copy a verbatim evidence quote      verify the quote exists in the transcript
                                    bookkeeping: status, cache, provenance
```

### 1. Schema-constrained output, validated again locally

The Pydantic model `MeetingExtraction` is sent to Gemini as its response schema
*and* used to validate the reply. Constrained decoding makes malformed output
rare but does not guarantee its absence, so the local validation is the trust
boundary. One re-roll is allowed for invalid JSON; a second failure is a real
error (`502 llm_invalid_response`).

The schema is kept deliberately loose (plain strings, no length limits or
patterns): provider support for JSON-Schema constraints is partial, and strictness
at this layer would make one malformed deadline discard every correct item
alongside it.

### 2. Field-level normalisation (`normalise_extraction`)

Strictness is applied per field, after validation. An unparseable deadline
becomes `null` with a warning; the task survives. A deadline more than 30 days
before or 3 years after the meeting is treated as a mis-resolution and
discarded. Warnings are returned to the caller, not swallowed.

### 3. Evidence verification (`services/grounding.py`)

Each decision and action item carries a verbatim `evidence_quote`. It is
checked mechanically against the transcript: exact match after normalisation,
or, near a matching run of at least 4 tokens, at least 80% token coverage.
The result is stored as `evidence_verified`.

An unverified item is **kept and flagged**, not deleted. Deleting would turn
every loosely-quoted but real task into a silent miss, which for a productivity
tool is worse than a visible "unverified" marker. The verified rate is a
measurable grounding metric.

### 4. Owner linking without guessing

Owner names are linked to participant rows by exact normalised match, or by a
unique first name. If two participants share a first name the owner is left
unlinked. The raw `owner_name` is always kept, so nothing is lost when linking
fails.

### 5. Prompt design

- A versioned system instruction (`extract-v1`) with explicit rules: no
  invention, what counts as a decision vs a proposal, owner must be one named
  person, priority only when explicitly expressed.
- The meeting date and weekday are given, so relative dates are resolvable.
- The transcript is fenced between explicit delimiters and declared untrusted;
  instructions inside it must not be followed.

### 6. Provenance and idempotency

Every summary stores provider, model, prompt version, transcript SHA-256, token
counts, and latency. When all of these match the current state, `/process`
returns the stored result without calling the LLM (observed: 211 ms vs 19.5 s).
Editing the transcript marks the summary `is_stale`.

## Alternatives considered

**Free-text output parsed with regex.** Fragile, and indistinguishable from
the model simply being wrong. Rejected outright by the project rules.

**Trust the model's JSON as-is.** Simplest. Rejected: the first live run already
produced an invented priority, and a single bad date would otherwise reach the
database unchecked.

**A second LLM call to verify the first ("LLM-as-judge").** Doubles cost and
latency, and moves the trust problem rather than solving it. String-level
evidence checking is free, deterministic, and explainable. An LLM judge is a
candidate for offline *evaluation*, not for the production path.

**Reject unverified items.** Rejected for the false-negative reason above.

## Consequences

**Advantages**
- Every stored item is traceable to a transcript passage, a model, and a
  prompt version.
- The deterministic half is covered by fast unit tests that need no API key.
- Re-processing an unchanged meeting costs nothing.

**Limitations**
- Evidence verification proves a passage *exists*, not that it *supports* the
  claim drawn from it. A real quote attached to a wrong conclusion still
  verifies.
- Re-processing replaces extracted items, which resets manual status changes.
  The API requires `force=true` for an unchanged transcript for this reason.
- Deadlines are dates without a time zone; overdue is computed in UTC.
- Processing is synchronous in M2 (5-30 s per request). M3 moves it to a
  background job. *(Superseded by [ADR 0008](0008-dynamodb-job-queue-with-leased-workers.md): processing is now queued and `POST /process` returns in ~80 ms.)*
- Owner extraction depends on names appearing in the transcript. There is no
  speaker diarisation.

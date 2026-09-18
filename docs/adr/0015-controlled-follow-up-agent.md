# ADR 0015: A controlled follow-up agent, with human approval

- **Status:** Accepted
- **Date:** 2026-09-18
- **Milestone:** M9
- **Builds on:** ADR 0007 (structured output with checks in code), ADR 0013 (Minutes of Meeting), ADR 0014 (RAG retrieval and citations)

## Context

The core workflow turns each meeting into minutes. Search and Ask your meetings
(ADR 0014) give MinuteAI a memory. What remained was acting on that memory:
noticing work that is late, decisions nobody confirmed, and topics that keep
coming back, and helping the user follow up.

The rule-based review flags from M7 (missing owner, unresolved deadline wording,
unverified quote) are **validation**. They check one meeting's extraction and
stay as deterministic code. The agent is a separate component that works across
meetings over time.

Five constraints shape the design:

1. **Never act without a person.** The agent must not send messages or change
   meeting data. Its only output is a proposal that a person approves (optionally
   after editing it) or rejects.
2. **Code decides what the agent may act on.** The model must not invent tasks,
   people or evidence.
3. **Bounded and auditable.** Each run has a fixed budget and a recorded trace
   of what it looked at and why.
4. **Useful when the model is down.** A quota or outage must not leave the user
   with nothing.
5. **No nagging.** A situation is proposed once. A rejection is final.

## Decision

### The loop

```
observe ─► deduplicate ─► prioritise ─► recall ─► decide + draft ─► verify ─► propose
 (SQL      (dedupe_key     (urgency,     (RAG        (ONE structured   (code      (proposals
 detectors) vs. all past   budget of     retrieve_   model call for    guard-     for a person
            proposals)     10)           context)    every candidate)  rails)     to decide)
```

`POST /api/v1/agent/runs` runs the loop synchronously for the signed-in user and
returns the run with its trace. The steps are:

| Step | What happens | Who decides |
|---|---|---|
| **observe** | Five detectors (plain SQL, joined on `meetings.owner_id`): overdue action items; items due within `AGENT_DUE_SOON_DAYS` (2); open items with no owner; decisions still `open` `AGENT_DECISION_STALE_DAYS` (14) after the meeting; pending items from minutes in the last `AGENT_LOOKBACK_DAYS` (60). A pending item that semantic search finds in another meeting's pending items (score ≥ `AGENT_RECURRING_MIN_SCORE`, 0.6) becomes one **recurring topic** instead of one candidate per meeting. | Code |
| **deduplicate** | Each candidate has a stable `dedupe_key` (for example `overdue:{item}:{deadline}`). Keys already proposed are dropped, whether the proposal was approved, pending or rejected. A new deadline gives a new key, so a new situation is a new proposal. | Code, enforced by `UNIQUE (owner_id, dedupe_key)` |
| **prioritise** | Overdue, then due soon, then recurring, unassigned, open decisions and unresolved topics. Within a kind, the most urgent comes first. At most `AGENT_MAX_CANDIDATES` (10) per run; the rest wait for the next run. | Code |
| **recall** | `rag.retrieve_context` (ADR 0014) returns up to `AGENT_CONTEXT_PASSAGES` (3) related passages per candidate: the same owner-filtered search, relevance gate and live-record context as Ask your meetings. | Code |
| **decide + draft** | **One** structured Gemini call (`AgentDrafts`, prompt `agent-v1`, temperature 0.2) covers every candidate. For each one it returns whether to follow up, priority, a cited rationale, recipients, and a short subject and message. | Model |
| **verify** | See the guardrails below. | Code |
| **propose** | A `follow_up_proposals` row for each outcome that still needs a follow-up. | Code |

### Guardrails (code, not prompt)

- Drafts for candidate ids that were not given are ignored, and so is a second draft for the same id.
- Recipients are limited to the candidate's allowed list: the action item's owner, or else the meeting's participants. Matching is case-insensitive, and the stored spelling is canonical.
- Citations must name a passage that was shown for that candidate; others are removed.
- "No follow-up needed" is accepted **only with a valid citation** as evidence. Without one, the proposal is kept and the trace records why.
- An empty or over-long draft is replaced by a fixed template built only from the candidate's facts.
- Any `LLMError` (quota, outage, invalid response) produces template drafts for every candidate. The run is marked `used_fallback`, and each proposal shows `drafted_by = template`.
- Facts, passages and names go into the prompt inside fences and are marked untrusted, so text inside a meeting cannot instruct the agent.

### Human approval

`POST /agent/proposals/{id}/approve` takes an optional edited subject and body,
plus a note. The original draft is kept beside the final text. `reject` records
an optional note. Each proposal can be decided **once** (409
`proposal_already_decided`). Another user's proposal returns 404 (ADR 0004).
MinuteAI does not send the message: the approved text is offered as a mail link
or copied, so it is sent from the user's own account.

### Storage

PostgreSQL, next to the records the proposals refer to (ADR 0001):

- `agent_runs`: trigger, status, model, prompt version, counts, `used_fallback`, `steps` (JSONB trace) and `error_code`. A partial unique index `WHERE status = 'running'` allows **one running run per user**. A second request gets 409 `agent_run_in_progress`. A run left `running` for 10 minutes (for example after a process restart) is marked `failed`/`interrupted` when the next run starts.
- `follow_up_proposals`: kind, dedupe key, priority, title, rationale, recipients, draft, `drafted_by`, the cited passages, decision fields, and foreign keys:
  - `meeting_id` uses CASCADE, so deleting a meeting deletes its proposals.
  - `action_item_id` and `decision_id` use SET NULL, so reprocessing a meeting keeps the proposal and its text.
  - `run_id` uses SET NULL.

## Alternatives considered

- **A tool-calling loop where the model chooses the next action.** This was rejected. The work is a fixed pipeline, and letting the model pick tools adds cost, latency and ways to fail without improving the result. Here the model is used where it adds value, judgement and wording, inside limits code can check.
- **One model call per candidate.** Rejected: up to ten times the quota for the same result. One call with a strict schema costs the same as one Ask question.
- **Sending follow-ups automatically.** Rejected by constraint 1. An approval step is also the natural place to correct a wrong recipient or tone.
- **Scheduled runs.** A run is started by the user. The runner takes a `trigger` field, so a scheduler can call the same function without other changes.

## Consequences

- The agent reuses M8 retrieval as-is; `retrieve_context` was extracted from `ask()` with no change in behaviour (the M8 tests are unchanged).
- Tests (`tests/test_agent.py`) cover:
  - detection of every kind, including recurring topics;
  - one model call per run;
  - every guardrail, the template fallback and a failed run;
  - the candidate budget, deduplication including after a rejection, and a new deadline creating a new proposal;
  - approve with edits, reject and decide-once;
  - cross-user isolation, single flight with stale-run recovery, and cascade and SET NULL behaviour.

  Eleven guardrail mutations were each confirmed to fail the suite.
- The dashboard reports `follow_ups_pending`, shown as a badge on the Follow-ups entry in the sidebar.

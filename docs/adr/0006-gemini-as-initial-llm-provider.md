# ADR 0006 — Google Gemini as the initial LLM and transcription provider

- **Status:** Accepted — supersedes the Groq choice from the architecture review
- **Date:** 2026-09-13
- **Milestone:** Decided before M2; first used in M2

## Context

The project brief allowed either Groq or Gemini as the single initial
provider. The architecture review picked Groq because it hosts both an LLM
and Whisper transcription under one key. Before any provider code was written,
the choice was revisited at the user's request.

What the pipeline needs from the provider:

1. **Structured extraction** (M2): summary, decisions, and action items as
   JSON that validates against Pydantic schemas.
2. **Long inputs** (M2): a one-to-two-hour transcript can run to tens of
   thousands of tokens.
3. **Transcription** (M4): meeting audio to text.
4. **Grounded generation** (M7) and **tool calling** (M8).
5. A free tier good enough for development and evaluation.

## Decision

Use the **Google Gemini API** through Google's official `google-genai` Python
SDK, behind the provider-neutral `LLMService` interface planned for M2.

- The model name is **configuration** (`GEMINI_MODEL`), not code. It is checked
  against the models the API key can actually use when M2 starts, instead of
  being assumed here, because Gemini model names change over time.
- Embeddings stay on local `sentence-transformers` (384 dimensions, ADR 0002).
  Gemini also offers embeddings, but switching would change the vector size
  and add network calls and rate limits to every chunk. This decision does not
  touch embeddings.

## Why Gemini fits

- **Native structured output.** Gemini can be given a response schema and
  asked for JSON only. That fits the "validate, don't parse free text" rule
  directly. Pydantic validation stays in place as the final check anyway.
- **Very large context window.** Whole transcripts fit in one request, so M2
  doesn't need a map-reduce summarisation step for long meetings.
- **Multimodal input.** Audio files can go straight to the same model, so one
  provider and one key still cover both extraction and transcription. That was
  Groq's main advantage.
- **Tool calling** is supported for the M8 agent.
- **Free tier** through Google AI Studio.

## Alternatives considered

**Groq (the original choice).** Much faster inference, and hosted Whisper gives
better-structured transcripts with timestamps. Not rejected on merit. It stays
the natural second implementation of `LLMService` if Gemini's rate limits or
transcription quality become a problem.

**Both at once.** Rejected by the brief's one-provider-initially rule. The
interface keeps a second provider a small, contained addition.

**Local models (Ollama).** No rate limits and no data leaves the machine, but
extraction quality on consumer hardware is much weaker, and CPU-only inference
on hour-long transcripts is slow. Rejected for the MVP.

## Consequences

**Advantages**
- One key covers extraction, transcription, RAG generation, and agent tool
  calls.
- Schema-constrained JSON output lowers the rate of LLM responses that fail
  validation.
- Long transcripts need no chunked summarisation.

**Limitations and risks**
- **Transcription is not Whisper.** Gemini returns text from audio, but its
  timestamps and speaker turns are less reliable than a dedicated ASR model.
  M4 keeps local `faster-whisper` as a fallback behind the same interface, and
  M12 compares the two with word error rate.
- **Free-tier rate limits** cap requests per minute and per day. Mitigations
  planned for M2: cache extraction results by transcript hash plus prompt
  version, back off on HTTP 429, and never re-process unchanged transcripts.
- **Data handling.** Google's terms treat free-tier and paid-tier API content
  differently, and free-tier content may be used to improve Google's products.
  Check the current terms before processing real, confidential meetings. Use
  synthetic or consented meeting data for development and evaluation.
- **Latency** is higher than Groq's. It doesn't matter for the async pipeline
  in M3, but it's noticeable for interactive RAG answers.

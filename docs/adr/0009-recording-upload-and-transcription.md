# ADR 0009 — Recording upload to S3 and Gemini transcription

- **Status:** Accepted
- **Date:** 2026-09-13
- **Milestone:** M4

## Context

M4 adds meeting recordings as an input. That raises four separate questions:
how bytes reach storage, how uploads are validated, which S3 implementation to
use locally without an AWS account, and how transcription fits the existing job
pipeline.

## Decision 1 — Browser uploads straight to S3 via presigned POST

```
browser ──POST /media/upload-url──► API ── returns presigned POST + signed upload token
browser ──multipart POST file──────► S3   (storage enforces the policy)
browser ──POST /media/complete─────► API ── HEAD + ranged GET → validate → record → queue job
```

The API never handles recording bytes: a 200 MB upload does not tie up an API
worker or its memory. Measured live: 4.9 MB uploaded in 0.72 s.

**Presigned POST rather than presigned PUT**, because a POST policy lets
*storage itself* enforce conditions a PUT URL cannot express:

| Policy condition | Stops |
|---|---|
| exact `key` | writing outside the issued path (another user's prefix) |
| exact `Content-Type` | uploading `text/html` under an audio URL |
| `content-length-range 1..MEDIA_MAX_BYTES` | multi-gigabyte uploads |

All three were verified against the storage server, and are re-verified in the
test suite by sending policy-violating uploads directly to it.

**The upload token** is a short-lived JWT (`type=media_upload`) naming the
user, meeting, media id, key, and content type. `complete` accepts nothing
else, so a client cannot confirm an object it wasn't issued, or reuse an access
token as an upload token.

**No database row until an upload is confirmed.** An earlier draft wrote a
`pending_upload` row when the URL was issued. That would have forced a
replacement upload to overwrite the current recording's row before the new
file was verified. Keeping the row out until confirmation removes that failure
mode and the status column with it. The trade-off is that issued-but-never-used
uploads can leave orphaned objects, to be expired by an S3 lifecycle rule in
M10.

## Decision 2 — Three independent validation layers

1. **Allow-list** of declared MIME types (audio/video containers only).
2. **Storage policy**: key, type, and size (above).
3. **File-signature sniffing** after upload: the first 64 bytes must match the
   declared container (RIFF/WAVE, ID3 or MPEG frame sync, OggS, fLaC, EBML,
   ISO-BMFF `ftyp`).

Layer 3 exists because layers 1 and 2 only check *declared* metadata. An HTML
file sent with `Content-Type: audio/wav` passes both. It fails layer 3, and the
object is deleted before anything else reads it. Tested, and mutation-checked:
disabling the sniffer makes four tests fail.

Uploaded filenames are kept for display only, sanitised, and never used in a
storage key or filesystem path. Keys are built from server-generated UUIDs.

## Decision 3 — RustFS for local S3, not MinIO or LocalStack

The architecture review assumed MinIO. At implementation time:

| Candidate | Finding |
|---|---|
| `minio/minio` | **Withdrawn from Docker Hub** (`repository does not exist`) |
| `quay.io/minio/minio` | Pullable, but last updated 2025-09-07: no security updates |
| `localstack/localstack` | **Refuses to start without a licence token**: a new credential requirement for local development |
| `rustfs/rustfs` | Maintained; passed a **12-point compatibility probe**, including policy enforcement, CORS preflight, ranged GET, presigned GET, and prefix delete |

RustFS is pinned to `1.0.0-rc.6`. Being a release candidate is acceptable
because it runs **only in development**: the application code is plain boto3
S3, and M10 changes `S3_ENDPOINT_URL` to real S3 with no code changes.

## Decision 4 — Transcription as a stage inside the existing job

A recording upload auto-queues the normal processing job (ADR 0008). The worker
adds a transcription stage before extraction, recorded in the job timeline
(`transcription_started`, `transcription_completed`). Retries, leases, fencing,
and failure classification all apply unchanged.

**When transcription runs** is decided by one rule:

| Recording | Transcript | Transcribe? |
|---|---|---|
| none | — | no |
| present | none | yes |
| present | typed by a person | **no: human input wins** |
| present | transcribed from this exact object (same etag) | no: cached |
| present | transcribed from a replaced recording | yes |

The ETag pins the exact stored object. A retry after an extraction failure, or
a forced re-extraction, never pays for transcription twice. Uploading a
recording when a typed transcript exists is refused (`409
manual_transcript_exists`) unless the client explicitly asks to replace it:
silently discarding human-entered text is worse than an extra click.

**Model:** `gemini-3.6-flash` via the Gemini Files API (inline requests cap at
~20 MB). `gemini-3.5-transcribe` was tested and rejected: it does not support
JSON output. The remote copy is deleted after every call.

The transcript is rendered as `Speaker: words` lines, the same shape as a
pasted transcript. The M2 extraction prompt, owner linking, and evidence
verification therefore run unchanged. The full structured output (segments,
speakers, timestamps) is archived to S3 at
`users/{user}/meetings/{meeting}/transcript/{media}.json`.

## Measured (live, 154 s synthetic recording)

| Metric | Result |
|---|---|
| Word error rate vs script | 2.7% (includes a 4-word intro absent from the reference) |
| Speaker naming | All four named from context; one run spelled "Meera" as "Mira" |
| Speaker attribution | **Two turns attributed to the wrong person** |
| Model timestamps | **Unreliable**: ran past 200 s on a 154 s recording |
| Transcription time | 23–31 s |
| Upload → full results | 47 s |
| Extraction quality on transcribed text | 2/2 decisions, 3/3 action items, owners linked, evidence verified |

## Alternatives considered

**Upload through the API (multipart).** Simplest client code. Rejected: every
byte of every recording would pass through an API process, memory and
connection time scale with file size, and the storage-enforced policy would be
lost.

**Local faster-whisper as the transcriber.** ADR 0006 planned it as a fallback.
**Deferred to M12**, where it becomes an evaluation baseline for WER, not a
runtime path. It brings a large native dependency (ctranslate2, model
downloads) with uncertain Windows wheels, and delivers no user-visible benefit
while Gemini is available. `TranscriptionProvider` is a separate interface from
`LLMProvider` so it can be added without touching the pipeline.

**Store transcripts only in S3.** Rejected: the transcript is read on every
extraction and will be chunked for embeddings in M6. It belongs in the
relational store; S3 keeps the raw, larger structured output.

## Consequences

**Advantages**
- Recordings never pass through the API; limits are enforced by storage.
- Disguised files are rejected by content, not by name.
- Transcription reuses every M3 reliability property (retries, crash recovery).
- Replacing or re-processing never double-charges transcription.
- No code change is needed to move from local storage to real S3.

**Limitations**
- **Speaker attribution is imperfect**, and owner linking inherits those errors.
  Measure in M12.
- **Name spelling varies** between runs ("Meera" / "Mira"), which can break
  owner matching against known participants.
- **Duration** is exact for WAV only; other containers report none rather than
  trusting model timestamps.
- **Recordings are sent to Google** for transcription (free-tier data terms
  apply; ADR 0006).
- **Orphaned objects** from abandoned uploads need an S3 lifecycle rule (M10).
- The local storage server is a release candidate; production uses Amazon S3.

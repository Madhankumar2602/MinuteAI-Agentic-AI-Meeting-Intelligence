"""Recording → transcript: the first stage of processing for audio/video meetings.

When is a recording transcribed?

    uploaded recording?   transcript?                                       →  transcribe?
    no                    -                                                  →  no
    yes                   none                                               →  yes
    yes                   MANUAL (typed/pasted by a person)                  →  no   ← human input wins
    yes                   TRANSCRIPTION of this exact object (same etag)     →  no   ← cached
    yes                   TRANSCRIPTION of a different/replaced recording    →  yes

The etag pins the exact stored object, so replacing a recording (even with a
file of the same name) triggers a fresh transcription, while re-processing the
same recording never pays for transcription twice.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
import wave
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    Meeting,
    MeetingMedia,
    MeetingStatus,
    Transcript,
    TranscriptSource,
)
from app.schemas.transcript import MIN_TRANSCRIPT_CHARS
from app.schemas.transcription import TranscriptionResult
from app.services.intelligence import MeetingNotFoundError
from app.services.llm.base import LLMResponseError, TranscriptionProvider
from app.services.storage import ObjectStorage, raw_transcript_key

logger = get_logger(__name__)


async def media_needing_transcription(
    db: AsyncSession, meeting_id: uuid.UUID
) -> MeetingMedia | None:
    media = await db.scalar(select(MeetingMedia).where(MeetingMedia.meeting_id == meeting_id))
    if media is None:
        return None
    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        return media
    if transcript.source == TranscriptSource.MANUAL:
        return None
    if transcript.media_id == media.id and transcript.media_etag == media.etag:
        return None
    return media


def render_transcript(result: TranscriptionResult) -> str:
    """Plain text in the same ``Speaker: words`` shape as a pasted transcript.

    Using the same shape means the M2 extraction prompt, owner matching, and
    evidence verification work unchanged on transcribed meetings.
    """
    lines = []
    for segment in result.segments:
        text = " ".join(segment.text.split())
        if text:
            speaker = " ".join(segment.speaker.split()) or "Speaker"
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def wav_duration_seconds(path: Path) -> int | None:
    """Exact duration from the WAV header.

    The model's own timestamps are not used for this: in live testing they ran
    past 200 s on a 154 s recording. Other containers would need a media
    library to read reliably, so they report no duration rather than a guess.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            return round(wav.getnframes() / wav.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


async def transcribe_meeting(
    db: AsyncSession,
    *,
    meeting_id: uuid.UUID,
    media: MeetingMedia,
    storage: ObjectStorage,
    transcriber: TranscriptionProvider,
) -> Transcript:
    meeting = await db.scalar(select(Meeting).where(Meeting.id == meeting_id))
    if meeting is None:
        raise MeetingNotFoundError()
    owner_id = meeting.owner_id
    media_id, media_key, media_etag, content_type = (
        media.id,
        media.s3_key,
        media.etag,
        media.content_type,
    )

    meeting.status = MeetingStatus.PROCESSING
    await db.commit()
    logger.info(
        "transcription started", extra={"meeting_id": str(meeting_id), "media_id": str(media_id)}
    )

    # The recording is downloaded to a private temporary directory, which is
    # removed (with the file) as soon as transcription returns or fails.
    with tempfile.TemporaryDirectory(prefix="minuteai-") as tmp:
        local = Path(tmp) / f"recording.{media_key.rsplit('.', 1)[-1]}"
        await storage.download_to_file(media_key, local)
        duration = wav_duration_seconds(local) if content_type == "audio/wav" else None
        result = await transcriber.transcribe(audio_path=local, mime_type=content_type)

    content = render_transcript(result.data)
    if len(content) < MIN_TRANSCRIPT_CHARS:
        raise LLMResponseError(
            "No usable speech was found in the recording.", code="no_speech_detected"
        )
    if len(content) > settings.transcript_max_chars:
        raise LLMResponseError(
            "The recording produced a transcript longer than the configured limit.",
            code="transcript_too_long",
        )

    raw_key = raw_transcript_key(user_id=owner_id, meeting_id=meeting_id, media_id=media_id)
    await storage.put_json(
        raw_key,
        {
            "media_id": str(media_id),
            "media_etag": media_etag,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            **result.data.model_dump(),
        },
    )

    transcript = await db.scalar(select(Transcript).where(Transcript.meeting_id == meeting_id))
    if transcript is None:
        transcript = Transcript(meeting_id=meeting_id)
        db.add(transcript)
    transcript.content = content
    transcript.content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    transcript.char_count = len(content)
    transcript.word_count = len(content.split())
    transcript.language = (result.data.language or None) and result.data.language[:16]
    transcript.source = TranscriptSource.TRANSCRIPTION
    transcript.media_id = media_id
    transcript.media_etag = media_etag
    transcript.raw_s3_key = raw_key
    transcript.transcription_model = result.model
    transcript.duration_seconds = duration
    await db.commit()

    logger.info(
        "transcription stored",
        extra={
            "meeting_id": str(meeting_id),
            "segments": len(result.data.segments),
            "words": transcript.word_count,
            "duration_s": duration,
        },
    )
    return transcript

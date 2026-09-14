"""Audio preparation for transcription: extract the audio track from a video (M7).

    video (MP4 / MOV / WebM) ─► decode audio ─► 16 kHz mono ─► Opus 32 kb/s in Ogg

Why extract instead of sending the video to Gemini:

* Size. A 10-minute 720p screen recording is ~100 MB; its audio track as Opus
  is ~2.5 MB, so the provider upload is roughly 40x smaller and faster.
* Privacy. Slides, faces, and screen content never leave the system; only the
  speech needed for minutes does.
* Cost and limits. Video is billed and rate-limited as video frames.

16 kHz mono is what speech recognition works at; Opus at 32 kb/s is
transparent for speech. PyAV bundles FFmpeg in its wheels, so there is no
system FFmpeg to install on Windows, in a container, or in Lambda.

The exact duration is counted from the decoded samples, for every format, so
the minutes report real recording length rather than the model's timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av

from app.core.exceptions import AppError

TARGET_RATE = 16_000
TARGET_BITRATE = 32_000
EXTRACTED_MIME = "audio/ogg"
EXTRACTED_SUFFIX = ".ogg"


class NoAudioTrackError(AppError):
    status_code = 422
    code = "no_audio_track"
    message = "The video has no audio track, so there is nothing to transcribe."


class MediaDecodeError(AppError):
    status_code = 422
    code = "media_unreadable"
    message = "The recording could not be decoded."


@dataclass(frozen=True, slots=True)
class ExtractedAudio:
    path: Path
    mime_type: str
    duration_seconds: int
    size_bytes: int


def extract_audio(source: Path, destination_dir: Path) -> ExtractedAudio:
    """Decode the first audio stream of ``source`` and write speech-grade Opus."""
    destination = destination_dir / f"audio{EXTRACTED_SUFFIX}"
    samples = 0
    try:
        with av.open(str(source)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise NoAudioTrackError()
            resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
            with av.open(str(destination), mode="w", format="ogg") as out:
                encoder = out.add_stream("libopus", rate=TARGET_RATE)
                encoder.bit_rate = TARGET_BITRATE
                encoder.layout = "mono"
                for frame in container.decode(stream):
                    frame.pts = None  # let the resampler re-time frames
                    for resampled in resampler.resample(frame):
                        samples += resampled.samples
                        out.mux(encoder.encode(resampled))
                for resampled in resampler.resample(None):  # flush
                    samples += resampled.samples
                    out.mux(encoder.encode(resampled))
                out.mux(encoder.encode(None))
    except (NoAudioTrackError, MediaDecodeError):
        raise
    except av.FFmpegError as exc:
        raise MediaDecodeError() from exc
    if samples == 0:
        raise NoAudioTrackError()
    return ExtractedAudio(
        path=destination,
        mime_type=EXTRACTED_MIME,
        duration_seconds=round(samples / TARGET_RATE),
        size_bytes=destination.stat().st_size,
    )


def media_duration_seconds(path: Path) -> int | None:
    """Duration from the container, for audio files that are sent as they are."""
    try:
        with av.open(str(path)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is not None and stream.duration and stream.time_base:
                return round(float(stream.duration * stream.time_base))
            if container.duration:
                return round(container.duration / av.time_base)
    except av.FFmpegError:
        return None
    return None

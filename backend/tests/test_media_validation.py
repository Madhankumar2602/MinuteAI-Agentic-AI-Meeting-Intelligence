"""Upload validation and transcript rendering - pure functions, no I/O."""

import wave
from pathlib import Path

import pytest

from app.schemas.transcription import TranscriptionResult, TranscriptSegment
from app.services.media_validation import (
    content_matches,
    resolve_format,
    safe_display_name,
    sniff_container,
)
from app.services.transcription import render_transcript, wav_duration_seconds
from tests.fakes import TINY_WAV

SIGNATURES = {
    "wav": TINY_WAV[:64],
    "mp3": b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 20,
    "mp3-sync": b"\xff\xfb\x90\x64" + b"\x00" * 20,
    "ogg": b"OggS\x00\x02" + b"\x00" * 20,
    "flac": b"fLaC\x00\x00\x00\x22" + b"\x00" * 20,
    "webm": b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81" + b"\x00" * 20,
    "m4a": b"\x00\x00\x00\x20ftypM4A \x00\x00\x02\x00" + b"\x00" * 20,
    "mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 20,
    "mov": b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00" + b"\x00" * 20,
}


@pytest.mark.parametrize(
    ("declared", "sample"),
    [
        ("audio/wav", "wav"),
        ("audio/x-wav", "wav"),
        ("audio/mpeg", "mp3"),
        ("audio/mpeg", "mp3-sync"),
        ("audio/ogg", "ogg"),
        ("audio/flac", "flac"),
        ("audio/webm", "webm"),
        ("video/webm", "webm"),
        ("audio/x-m4a", "m4a"),
        ("video/mp4", "mp4"),
        ("video/quicktime", "mov"),
    ],
)
def test_genuine_recordings_are_accepted(declared: str, sample: str) -> None:
    fmt = resolve_format(declared)
    assert fmt is not None
    assert content_matches(fmt, SIGNATURES[sample])


def test_html_disguised_as_audio_is_rejected() -> None:
    """Declared type passes the allow-list; the bytes give it away."""
    fmt = resolve_format("audio/wav")
    assert not content_matches(fmt, b"<!doctype html><script>alert(1)</script>")
    assert sniff_container(b"<!doctype html>") is None


def test_real_container_with_wrong_declared_type_is_rejected() -> None:
    assert not content_matches(resolve_format("audio/mpeg"), SIGNATURES["wav"])
    assert not content_matches(resolve_format("audio/wav"), SIGNATURES["ogg"])


@pytest.mark.parametrize(
    "declared", ["text/html", "application/pdf", "image/png", "application/octet-stream", ""]
)
def test_non_media_types_are_not_allowed(declared: str) -> None:
    assert resolve_format(declared) is None


def test_mime_parameters_and_case_are_tolerated() -> None:
    fmt = resolve_format("Audio/WebM; codecs=opus")
    assert fmt is not None and fmt.extension == "webm"


def test_empty_or_truncated_head_is_rejected() -> None:
    assert sniff_container(b"") is None
    assert not content_matches(resolve_format("audio/wav"), b"RIFF")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\Desktop\\standup.m4a", "standup.m4a"),
        ('weird<>:"|?*name.wav', "weirdname.wav"),
        ("line\nbreak.wav", "linebreak.wav"),
        ("   ", None),
        (None, None),
    ],
)
def test_display_filename_is_sanitised(raw: str | None, expected: str | None) -> None:
    assert safe_display_name(raw) == expected


def test_render_transcript_uses_pasted_transcript_shape() -> None:
    result = TranscriptionResult(
        language="en",
        segments=[
            TranscriptSegment(speaker=" Priya ", start_seconds=0, text="Okay,   let's start."),
            TranscriptSegment(speaker="Speaker 2", start_seconds=3, text="   "),
            TranscriptSegment(speaker="", start_seconds=5, text="Morning."),
        ],
    )
    assert render_transcript(result) == "Priya: Okay, let's start.\nSpeaker: Morning."


def test_wav_duration_is_read_from_the_header(tmp_path: Path) -> None:
    path = tmp_path / "three_seconds.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 3)
    assert wav_duration_seconds(path) == 3


def test_wav_duration_of_non_wav_is_none(tmp_path: Path) -> None:
    path = tmp_path / "not.wav"
    path.write_bytes(b"ID3 not really a wav")
    assert wav_duration_seconds(path) is None

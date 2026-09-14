"""Audio extraction from video: real media through the bundled FFmpeg."""

from pathlib import Path

import av
import pytest

from app.services.audio import (
    TARGET_RATE,
    NoAudioTrackError,
    extract_audio,
    media_duration_seconds,
)
from tests.media_factory import make_video

FIXTURE_WAV = Path(__file__).parent / "fixtures" / "audio" / "platform_sync.wav"


def test_extracts_speech_grade_opus_with_the_exact_duration(tmp_path: Path) -> None:
    video = tmp_path / "meeting.mp4"
    video.write_bytes(make_video(seconds=6))

    audio = extract_audio(video, tmp_path)

    assert audio.mime_type == "audio/ogg"
    assert audio.duration_seconds == 6
    assert audio.size_bytes < video.stat().st_size
    with av.open(str(audio.path)) as container:
        [stream] = container.streams
        assert stream.type == "audio"
        assert stream.codec_context.name == "opus"
        assert stream.codec_context.channels == 1
        # Opus always decodes at 48 kHz; the encoder was fed 16 kHz speech.
        decoded = sum(f.samples / f.sample_rate for f in container.decode(stream))
    assert abs(decoded - 6) < 0.2
    assert TARGET_RATE == 16_000


def test_a_video_without_sound_is_a_clear_permanent_error(tmp_path: Path) -> None:
    video = tmp_path / "silent.mp4"
    video.write_bytes(make_video(seconds=2, audio=False))
    with pytest.raises(NoAudioTrackError) as raised:
        extract_audio(video, tmp_path)
    assert raised.value.code == "no_audio_track"


def test_duration_is_read_from_the_container_for_audio_files() -> None:
    assert media_duration_seconds(FIXTURE_WAV) == 154  # matches the WAV header


def test_unreadable_media_reports_no_duration(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all" * 10)
    assert media_duration_seconds(junk) is None

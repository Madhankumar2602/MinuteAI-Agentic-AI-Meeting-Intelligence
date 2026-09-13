"""What counts as an acceptable meeting recording.

Three independent checks, because each can be lied about on its own:

1. The declared MIME type must be on the allow-list      (request validation)
2. Storage enforces that type and a size ceiling          (presigned POST policy)
3. The file's first bytes must match a known audio/video  (signature sniffing after upload)
   container signature

(3) is what stops ``evil.html`` uploaded as ``Content-Type: audio/wav``: the
declared type passes (1) and (2), but the bytes are not a WAV file. Such an
upload is rejected and the object deleted before anything else touches it.
Signature sniffing checks the container format, not that the audio is valid;
a corrupt file that starts correctly is caught later by transcription.
"""

from __future__ import annotations

from dataclasses import dataclass

SNIFF_BYTES = 64


@dataclass(frozen=True, slots=True)
class MediaFormat:
    mime_type: str
    extension: str
    kind: str  # "audio" | "video"


# Declared MIME type -> canonical format. Several aliases browsers actually send
# for the same container are accepted (e.g. audio/x-wav, audio/x-m4a).
ALLOWED_FORMATS: dict[str, MediaFormat] = {
    "audio/mpeg": MediaFormat("audio/mpeg", "mp3", "audio"),
    "audio/mp3": MediaFormat("audio/mpeg", "mp3", "audio"),
    "audio/wav": MediaFormat("audio/wav", "wav", "audio"),
    "audio/x-wav": MediaFormat("audio/wav", "wav", "audio"),
    "audio/wave": MediaFormat("audio/wav", "wav", "audio"),
    "audio/mp4": MediaFormat("audio/mp4", "m4a", "audio"),
    "audio/x-m4a": MediaFormat("audio/mp4", "m4a", "audio"),
    "audio/aac": MediaFormat("audio/aac", "aac", "audio"),
    "audio/ogg": MediaFormat("audio/ogg", "ogg", "audio"),
    "audio/webm": MediaFormat("audio/webm", "webm", "audio"),
    "audio/flac": MediaFormat("audio/flac", "flac", "audio"),
    "video/mp4": MediaFormat("video/mp4", "mp4", "video"),
    "video/webm": MediaFormat("video/webm", "webm", "video"),
    "video/quicktime": MediaFormat("video/quicktime", "mov", "video"),
}


def resolve_format(declared_mime: str) -> MediaFormat | None:
    return ALLOWED_FORMATS.get(declared_mime.split(";")[0].strip().lower())


def sniff_container(head: bytes) -> str | None:
    """Identify the container format from leading bytes. None if unrecognised."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3":
        return "mp3"  # MP3 with an ID3v2 tag
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # MPEG audio frame sync (MP3 without a tag) or ADTS AAC.
        return "aac" if (head[1] & 0x06) == 0 else "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"  # EBML (WebM / Matroska)
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        return "mov" if brand == b"qt  " else "mp4"  # ISO BMFF: mp4, m4a, mov
    return None


# Which sniffed containers are consistent with each canonical extension.
_COMPATIBLE: dict[str, set[str]] = {
    "wav": {"wav"},
    "mp3": {"mp3"},
    "aac": {"aac", "mp3"},  # ADTS and MPEG frame sync share a prefix
    "ogg": {"ogg"},
    "flac": {"flac"},
    "webm": {"webm"},
    "m4a": {"mp4"},
    "mp4": {"mp4", "mov"},
    "mov": {"mov", "mp4"},
}


def content_matches(fmt: MediaFormat, head: bytes) -> bool:
    detected = sniff_container(head)
    return detected is not None and detected in _COMPATIBLE[fmt.extension]


def safe_display_name(filename: str | None) -> str | None:
    """Keep an uploaded filename for display only: basename, printable, bounded.

    It is never used to build a storage key or a filesystem path.
    """
    if not filename:
        return None
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*').strip()
    return name[:255] or None

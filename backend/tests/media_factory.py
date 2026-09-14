"""Build small, real media files for tests with PyAV (FFmpeg), no fixtures on disk."""

from __future__ import annotations

import io
import math

import av
import numpy as np


def make_video(seconds: float = 2.0, *, audio: bool = True, tone_hz: float = 440.0) -> bytes:
    """An MP4 (MPEG-4 video + AAC audio) of a moving grey frame and a sine tone."""
    buffer = io.BytesIO()
    rate = 16_000
    with av.open(buffer, "w", format="mp4") as out:
        video = out.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 160, 120, "yuv420p"
        sound = out.add_stream("aac", rate=rate) if audio else None
        if sound is not None:
            sound.layout = "mono"
            total = int(seconds * rate)
            chunk = 1024
            for start in range(0, total, chunk):
                n = min(chunk, total - start)
                t = (np.arange(start, start + n) / rate).astype(np.float32)
                samples = (0.3 * np.sin(2 * math.pi * tone_hz * t)).astype(np.float32)
                frame = av.AudioFrame.from_ndarray(
                    samples.reshape(1, -1), format="fltp", layout="mono"
                )
                frame.sample_rate = rate
                frame.pts = start
                out.mux(sound.encode(frame))
            out.mux(sound.encode(None))
        for i in range(int(seconds * 10)):
            image = np.full((120, 160, 3), (i * 20) % 255, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = i
            out.mux(video.encode(frame))
        out.mux(video.encode(None))
    return buffer.getvalue()

"""Audio-extraction helpers for runners that feed a media file to the pipeline.

Shared by `youtube_segment` (remote URL) and `video_segment` (local file):
both decode a time range to raw PCM with ffmpeg and stream it to Deepgram.
"""

from __future__ import annotations

import asyncio
import re
import shutil


def parse_time(value: str) -> float:
    """Parse a time string into seconds.

    Accepts:
    - seconds as a number (e.g. "600", "600.5")
    - MM:SS (e.g. "10:00")
    - HH:MM:SS (e.g. "01:10:00")
    """
    value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)

    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Invalid time format: {value!r}")

    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid time format: {value!r}") from exc

    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


async def run_ffmpeg_pcm(
    *,
    source: str,
    start_seconds: float,
    duration_seconds: float | None,
    sample_rate: int,
    channels: int,
) -> asyncio.subprocess.Process:
    """Start ffmpeg decoding `source` (path or URL) to s16le PCM on stdout.

    `duration_seconds=None` decodes to the end of the input.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start_seconds}"]
    cmd += ["-i", source]
    if duration_seconds is not None:
        cmd += ["-t", f"{duration_seconds}"]
    cmd += [
        "-vn",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]

    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def read_all_stderr(proc: asyncio.subprocess.Process) -> str:
    if proc.stderr is None:
        return ""
    data = await proc.stderr.read()
    return data.decode("utf-8", errors="replace")

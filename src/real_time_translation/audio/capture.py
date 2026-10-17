"""Audio capture from various sources."""

import asyncio
import contextlib
import subprocess
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import rtms


class AudioSource(Protocol):
    """Protocol for audio sources."""

    async def read(self) -> bytes:
        """Read audio data chunk."""
        ...

    async def close(self) -> None:
        """Close the audio source."""
        ...


@dataclass
class ZoomRTMSConfig:
    """Configuration for Zoom RTMS connection."""

    client_id: str
    client_secret: str
    webhook_port: int = 8080
    webhook_path: str = "/webhook"


class AudioCapture(ABC):
    """Base class for audio capture implementations."""

    @abstractmethod
    async def start(self) -> None:
        """Start capturing audio."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop capturing audio."""
        ...

    @abstractmethod
    def stream(self) -> AsyncIterator[bytes]:
        """Stream audio data as async iterator.

        Yields:
            Audio data chunks as bytes (PCM 16-bit, 16kHz mono)
        """
        ...


class ZoomRTMSCapture(AudioCapture):
    """Audio capture from Zoom RTMS SDK.

    Uses the Zoom RTMS SDK to receive real-time audio streams
    from Zoom meetings via webhook events.

    See: https://github.com/zoom/rtms
    """

    def __init__(self, config: ZoomRTMSConfig) -> None:
        """Initialize Zoom RTMS capture.

        Args:
            config: RTMS configuration with credentials
        """
        self._config = config
        self._running = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._client: rtms.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Start capturing audio from Zoom meeting."""
        import rtms

        self._running = True
        self._loop = asyncio.get_running_loop()

        # Initialize RTMS client
        self._client = rtms.Client()

        # Register webhook handler
        @self._client.on_webhook_event()
        def handle_webhook(payload: dict) -> None:
            if payload.get("event") == "meeting.rtms_started":
                rtms_payload = payload.get("payload", {})
                self._client.join(
                    meeting_uuid=rtms_payload.get("meeting_uuid"),
                    rtms_stream_id=rtms_payload.get("rtms_stream_id"),
                    server_urls=rtms_payload.get("server_urls"),
                    signature=rtms_payload.get("signature"),
                )

        # Register audio data handler
        @self._client.onAudioData
        def on_audio(data: bytes, size: int, timestamp: int, metadata: object) -> None:
            # Put audio data into queue for async processing
            if self._loop and self._running:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, data)

        @self._client.onJoinConfirm
        def on_join(reason: str) -> None:
            print(f"Joined Zoom RTMS: {reason}")

        @self._client.onLeave
        def on_leave(reason: str) -> None:
            print(f"Left Zoom RTMS: {reason}")
            self._running = False

        # Start polling in background
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Background task to poll RTMS SDK events."""
        while self._running and self._client:
            try:
                self._client._process_join_queue()
                self._client._poll_if_needed()
                await asyncio.sleep(0.01)
            except Exception as e:
                print(f"RTMS poll error: {e}")
                await asyncio.sleep(0.1)

    async def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False
        if self._client:
            self._client.leave()
            self._client = None

    async def stream(self) -> AsyncIterator[bytes]:
        """Stream audio data from Zoom meeting.

        Yields:
            Audio data chunks as bytes
        """
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield chunk
            except TimeoutError:
                continue


class MicrophoneCapture(AudioCapture):
    """Audio capture from system microphone.

    This can be used for testing or for capturing system audio
    (e.g., from Zoom via virtual audio device).
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ) -> None:
        """Initialize microphone capture.

        Args:
            sample_rate: Audio sample rate in Hz
            channels: Number of audio channels
            chunk_size: Size of audio chunks in samples
        """
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size = chunk_size
        self._running = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self) -> None:
        """Start capturing audio from microphone."""
        self._running = True
        # TODO: Initialize PyAudio or sounddevice for microphone input
        # This requires additional dependencies (pyaudio or sounddevice)

    async def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False

    async def stream(self) -> AsyncIterator[bytes]:
        """Stream audio data from microphone.

        Yields:
            Audio data chunks as bytes
        """
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield chunk
            except TimeoutError:
                continue


class QueueAudioCapture(AudioCapture):
    """Audio capture backed by an in-memory queue.

    This is useful for web demos where audio chunks are pushed from
    an external source (e.g., Gradio streaming audio).
    """

    def __init__(self, max_queue_size: int = 50) -> None:
        """Initialize queue-based audio capture.

        Args:
            max_queue_size: Maximum number of audio chunks to buffer
        """
        self._running = False
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queue_size)

    async def start(self) -> None:
        """Start capturing audio."""
        self._running = True

    async def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False
        with contextlib.suppress(asyncio.QueueEmpty):
            while True:
                self._queue.get_nowait()

    async def stream(self) -> AsyncIterator[bytes]:
        """Stream audio data from the internal queue."""
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield chunk
            except TimeoutError:
                continue

    def push_audio(self, audio_data: bytes) -> None:
        """Push audio data into the queue.

        Args:
            audio_data: Raw PCM audio data
        """
        if not self._running:
            return
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(audio_data)


class FFmpegRTMPCapture(AudioCapture):
    """Audio capture from an RTMP source via ffmpeg."""

    def __init__(
        self,
        rtmp_url: str,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 20,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self._rtmp_url = rtmp_url
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_ms = chunk_ms
        self._ffmpeg_path = ffmpeg_path
        self._process: asyncio.subprocess.Process | None = None
        self._running = False

    async def start(self) -> None:
        """Start capturing audio by spawning ffmpeg."""
        cmd = [
            self._ffmpeg_path,
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            self._rtmp_url,
            "-f",
            "s16le",
            "-ar",
            str(self._sample_rate),
            "-ac",
            str(self._channels),
            "pipe:1",
        ]
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._running = True

    async def stop(self) -> None:
        """Stop capturing audio and terminate ffmpeg."""
        self._running = False
        if not self._process:
            return
        self._process.terminate()
        with contextlib.suppress(ProcessLookupError):
            await self._process.wait()
        self._process = None

    async def stream(self) -> AsyncIterator[bytes]:
        """Stream audio data from ffmpeg stdout."""
        if not self._process or not self._process.stdout:
            return

        bytes_per_sample = 2
        chunk_size = int(
            self._sample_rate
            * self._channels
            * bytes_per_sample
            * self._chunk_ms
            / 1000
        )

        while self._running and self._process.stdout:
            try:
                data = await self._process.stdout.read(chunk_size)
            except asyncio.CancelledError:
                break
            if not data:
                break
            yield data

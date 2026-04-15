"""Deepgram WebSocket client for real-time transcription."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from deepgram import AsyncDeepgramClient
@dataclass
class TranscriptionResult:
    """Result from transcription service."""

    text: str
    is_final: bool
    confidence: float
    start_time: float
    end_time: float

    @property
    def is_low_confidence(self) -> bool:
        """Check if confidence is below threshold."""
        return self.confidence < 0.7


class DeepgramTranscriber:
    """Deepgram WebSocket client for real-time transcription.

    Uses Deepgram's streaming API to transcribe audio in real-time.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        model: str = "nova-2-general",
        punctuate: bool = True,
        smart_format: bool = True,
        interim_results: bool = True,
        endpointing: int | None = 500,
        utterance_end_ms: int | None = None,
        keepalive_interval: float = 5.0,
        emit_interim: bool = False,
        vad_events: bool | None = None,
    ) -> None:
        """Initialize Deepgram transcriber.

        Args:
            api_key: Deepgram API key
            language: Language code for transcription
            model: Deepgram model to use
            punctuate: Whether to add punctuation
            smart_format: Whether to use Deepgram smart formatting
            interim_results: Whether to receive interim (non-final) results
            endpointing: Silence timeout in ms to finalize transcription
            utterance_end_ms: Model-based end-of-speech timeout in ms
            keepalive_interval: Keepalive interval in seconds
            emit_interim: Whether to emit interim results to consumers
            vad_events: Whether to enable VAD events in Deepgram
        """
        self._api_key = api_key
        self._language = language
        self._model = model
        self._punctuate = punctuate
        self._smart_format = smart_format
        self._interim_results = interim_results
        self._endpointing = endpointing
        self._utterance_end_ms = utterance_end_ms
        self._keepalive_interval = keepalive_interval
        self._emit_interim = emit_interim
        self._vad_events = vad_events

        self._client: AsyncDeepgramClient | None = None
        self._connection_cm: Any = None
        self._connection: Any = None
        self._listener_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._last_audio_at = 0.0
        self._pending_result: TranscriptionResult | None = None
        self._last_final_text: str | None = None
        self._last_final_at = 0.0
        self._running = False
        self._result_queue: asyncio.Queue[TranscriptionResult] = asyncio.Queue()
        self._on_transcript: Callable[[TranscriptionResult], None] | None = None

    async def connect(self) -> None:
        """Establish WebSocket connection to Deepgram."""
        self._client = AsyncDeepgramClient(api_key=self._api_key)

        def _bool_str(value: bool) -> str:
            return "true" if value else "false"

        options = {
            "model": self._model,
            "language": self._language,
            "punctuate": _bool_str(self._punctuate),
            "smart_format": _bool_str(self._smart_format),
            "interim_results": _bool_str(self._interim_results),
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
        }
        if self._endpointing is not None:
            options["endpointing"] = str(self._endpointing)
        if self._utterance_end_ms is not None:
            options["utterance_end_ms"] = str(self._utterance_end_ms)
        if self._vad_events is not None:
            options["vad_events"] = _bool_str(self._vad_events)

        self._connection_cm = self._client.listen.v1.connect(**options)
        self._connection = await self._connection_cm.__aenter__()
        self._running = True
        self._last_audio_at = time.monotonic()
        self._listener_task = asyncio.create_task(self._listen())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def finalize(self) -> None:
        """Signal end of audio stream to Deepgram and wait for final results."""
        if self._connection:
            try:
                # Send finalize signal to Deepgram
                await self._connection.finish()
                # Wait for the listener task to process final results
                if self._listener_task:
                    # Use keepalive interval to derive a reasonable timeout
                    timeout = max(self._keepalive_interval * 2, 3.0)
                    try:
                        await asyncio.wait_for(self._listener_task, timeout=timeout)
                    except asyncio.TimeoutError:
                        pass  # Timeout is expected; listener runs until cancelled
            except Exception:
                pass  # Ignore errors during finalize

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None
        if self._connection_cm:
            await self._connection_cm.__aexit__(None, None, None)
            self._connection_cm = None
            self._connection = None

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to Deepgram for transcription.

        Args:
            audio_data: Raw PCM audio data (16-bit, 16kHz, mono)
        """
        if self._connection and self._running:
            self._last_audio_at = time.monotonic()
            await self._connection.send_media(audio_data)

    def set_callback(self, callback: Callable[[TranscriptionResult], None]) -> None:
        """Set callback for transcription results.

        Args:
            callback: Function to call with transcription results
        """
        self._on_transcript = callback

    async def results(self) -> AsyncIterator[TranscriptionResult]:
        """Async iterator for transcription results.

        Yields:
            TranscriptionResult objects
        """
        while self._running:
            try:
                result = await asyncio.wait_for(self._result_queue.get(), timeout=1.0)
                yield result
            except TimeoutError:
                continue

    async def _listen(self) -> None:
        if not self._connection:
            return
        try:
            async for result in self._connection:
                self._handle_message(result)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._on_error(exc)

    async def _keepalive_loop(self) -> None:
        if not self._connection:
            return
        try:
            while self._running:
                await asyncio.sleep(self._keepalive_interval)
                if not self._connection or not self._running:
                    continue
                idle_time = time.monotonic() - self._last_audio_at
                if idle_time < self._keepalive_interval:
                    continue
                # Deepgram python SDK 3+ automatically handles keepalives. If needed, we can send a custom command or JSON string.
                # await self._connection.send_control({"type": "KeepAlive"})
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._on_error(exc)

    def _handle_message(self, result: Any) -> None:
        """Handle transcription message from Deepgram.

        Args:
            result: Deepgram transcription result
        """
        try:
            message_type = getattr(result, "type", None)
            if message_type == "UtteranceEnd":
                self._handle_utterance_end(result)
                return
            if message_type != "Results":
                return

            channel = result.channel
            alternative = channel.alternatives[0]
            transcript = alternative.transcript
            is_final = bool(getattr(result, "is_final", False))

            if not transcript:
                return

            transcript_result = TranscriptionResult(
                text=transcript,
                is_final=is_final,
                confidence=float(alternative.confidence),
                start_time=float(result.start),
                end_time=float(result.start + result.duration),
            )

            if transcript_result.is_final:
                self._pending_result = None
                self._emit_result(transcript_result)
            else:
                self._pending_result = transcript_result
                if self._emit_interim:
                    self._emit_result(transcript_result)

        except (AttributeError, IndexError):
            pass  # Ignore malformed results

    def _handle_utterance_end(self, result: Any) -> None:
        pending = self._pending_result
        if not pending or not pending.text:
            return

        now = time.monotonic()
        if self._last_final_text == pending.text and now - self._last_final_at < 1.0:
            self._pending_result = None
            return

        last_word_end = getattr(result, "last_word_end", None)
        end_time = (
            float(last_word_end)
            if isinstance(last_word_end, (int, float))
            else pending.end_time
        )
        final_result = TranscriptionResult(
            text=pending.text,
            is_final=True,
            confidence=pending.confidence,
            start_time=pending.start_time,
            end_time=end_time,
        )
        self._pending_result = None
        self._emit_result(final_result)

    def _emit_result(self, transcript_result: TranscriptionResult) -> None:
        if transcript_result.is_final:
            self._last_final_text = transcript_result.text
            self._last_final_at = time.monotonic()

        with contextlib.suppress(asyncio.QueueFull):
            self._result_queue.put_nowait(transcript_result)

        if self._on_transcript:
            self._on_transcript(transcript_result)

    def _on_error(self, error: Any) -> None:
        """Handle error from Deepgram.

        Args:
            error: Error information
        """
        print(f"Deepgram error: {error}")

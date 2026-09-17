"""Deepgram WebSocket client for real-time transcription."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
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
    # Identifies which spoken utterance this chunk belongs to (stable across
    # soft-finalized continuation chunks of the same utterance, see
    # `max_interim_duration`). Lets downstream consumers tell "more of the
    # same utterance is coming" apart from "this utterance is truly done"
    # instead of treating every `is_final=True` chunk as a standalone line.
    utterance_id: int = 0
    # True when this chunk is the genuine end of the utterance (Deepgram's
    # own `is_final`/`UtteranceEnd`). False when it's a soft-finalized
    # mid-utterance chunk emitted early (by `max_interim_duration`) purely
    # for latency -- more chunks for the same `utterance_id` are expected.
    is_utterance_end: bool = True

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
        model: str = "nova-3-general",
        punctuate: bool = True,
        smart_format: bool = True,
        interim_results: bool = True,
        endpointing: int | None = 500,
        utterance_end_ms: int | None = None,
        keepalive_interval: float = 5.0,
        emit_interim: bool = False,
        vad_events: bool | None = None,
        max_interim_duration: float | None = 4.0,
        keyterms: list[str] | None = None,
        local_agreement_commit: bool = False,
        confidence_early_commit_threshold: float | None = None,
        confidence_early_commit_min_elapsed: float = 2.0,
        completeness_check: Callable[[str], Awaitable[bool | None]] | None = None,
        semantic_gating_min_elapsed: float = 1.5,
        semantic_gating_check_interval: float = 1.5,
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
            max_interim_duration: Force-finalize (soft-final) an in-progress
                utterance after this many seconds even without a natural
                pause. Continuous fluent speech (common in lectures) can run
                well past `endpointing`/`utterance_end_ms` without ever
                triggering Deepgram's own `is_final`, which would otherwise
                silently withhold translation for the entire stretch. None
                disables this safety net.
            keyterms: Domain terms to bias the acoustic/language model
                toward (Deepgram Keyterm Prompting). Only supported on
                nova-3 models -- pass None/empty when using an older model.
            local_agreement_commit: Experimental (h-localagreement-asr-commit,
                research_agent/state/hypotheses.json). When True, also
                soft-finalize (commit) any word prefix that two consecutive
                interim hypotheses for the same utterance agree on
                (LocalAgreement-2), as soon as that agreement is observed,
                instead of relying solely on `max_interim_duration`'s fixed
                timer. False (default) preserves today's timer-only behavior.
            confidence_early_commit_threshold: Experimental
                (h-asr-confidence-early-commit,
                research_agent/state/hypotheses.json). When set, the
                periodic force-finalize check may soft-finalize BEFORE
                `max_interim_duration` once the pending interim's own
                Deepgram confidence is at or above this value (and at
                least `confidence_early_commit_min_elapsed` seconds have
                passed). None (default) disables early commit entirely.
            confidence_early_commit_min_elapsed: Minimum seconds an
                utterance must have been accumulating before a
                high-confidence early commit is allowed to fire.
            completeness_check: Experimental
                (h-semantic-completeness-gating, research_agent/state/
                hypotheses.json). Optional async callback (typically
                LLMTranslator.check_completeness) that, given the pending
                interim's text, returns True (confidently a complete
                clause -- safe to commit now), False (confidently
                incomplete), or None (unknown/error -- do nothing this
                round). When set, the periodic force-finalize check may
                soft-finalize BEFORE `max_interim_duration` once this
                returns True. None (default) disables semantic gating
                entirely.
            semantic_gating_min_elapsed: Minimum seconds an utterance must
                have been accumulating before the first completeness check
                is made.
            semantic_gating_check_interval: Minimum seconds between two
                completeness checks for the SAME utterance, so a
                low-confidence/False result doesn't trigger a check on
                every 0.5s poll tick.
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
        self._max_interim_duration = max_interim_duration
        self._keyterms = keyterms
        self._local_agreement_commit = local_agreement_commit
        self._confidence_early_commit_threshold = confidence_early_commit_threshold
        self._confidence_early_commit_min_elapsed = confidence_early_commit_min_elapsed
        self._completeness_check = completeness_check
        self._semantic_gating_min_elapsed = semantic_gating_min_elapsed
        self._semantic_gating_check_interval = semantic_gating_check_interval
        self._last_semantic_check_at: float | None = None
        self._prev_interim_words: list[str] | None = None

        self._client: AsyncDeepgramClient | None = None
        self._connection_cm: Any = None
        self._connection: Any = None
        self._listener_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._force_finalize_task: asyncio.Task[None] | None = None
        self._last_audio_at = 0.0
        self._pending_result: TranscriptionResult | None = None
        self._last_final_text: str | None = None
        self._last_final_at = 0.0
        self._running = False
        self._result_queue: asyncio.Queue[TranscriptionResult] = asyncio.Queue()
        self._on_transcript: Callable[[TranscriptionResult], None] | None = None

        # Utterance-level state for incremental (soft) finalization. Deepgram
        # sends cumulative transcripts per utterance (each interim/final
        # message repeats the whole utterance so far, not just new words),
        # so soft-finalizing early requires tracking how many words have
        # already been consumed/emitted to avoid re-translating them when
        # the next message repeats them.
        self._utterance_start_time: float | None = None
        self._consumed_word_count = 0
        self._consumed_end_time: float | None = None
        self._utterance_since: float | None = None
        self._utterance_id = 0

    async def connect(self) -> None:
        """Establish WebSocket connection to Deepgram."""
        self._client = AsyncDeepgramClient(api_key=self._api_key)

        def _bool_str(value: bool) -> str:
            return "true" if value else "false"

        def _build_options(keyterms: list[str] | None) -> dict[str, Any]:
            options: dict[str, Any] = {
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
            if keyterms:
                options["keyterm"] = keyterms
            return options

        # Deepgram enforces a cumulative *token* budget across all keyterms,
        # not just a term-count cap: a real-world dictionary with
        # multi-word/CJK terms has been observed to 400 well under 100
        # terms even though each individual term is well-formed (a 100-term
        # list failed; the same list truncated to 90 connected fine).
        # Modeling Deepgram's exact tokenizer isn't worth the effort here,
        # so back off by halving the list on failure instead -- an
        # ASR-quality optimization must never be able to take the whole
        # session down with it.
        active_keyterms = self._keyterms
        while True:
            try:
                self._connection_cm = self._client.listen.v1.connect(
                    **_build_options(active_keyterms)
                )
                self._connection = await self._connection_cm.__aenter__()
                break
            except Exception as exc:  # noqa: BLE001
                if not active_keyterms:
                    raise
                dropped = len(active_keyterms)
                active_keyterms = active_keyterms[: len(active_keyterms) // 2]
                print(
                    f"Deepgram connect failed with {dropped} keyterms active "
                    f"({exc}); retrying with {len(active_keyterms)}"
                )
        self._keyterms = active_keyterms
        self._running = True
        self._last_audio_at = time.monotonic()
        self._listener_task = asyncio.create_task(self._listen())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        if self._max_interim_duration is not None:
            self._force_finalize_task = asyncio.create_task(
                self._force_finalize_loop()
            )

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
        if self._force_finalize_task:
            self._force_finalize_task.cancel()
            await asyncio.gather(self._force_finalize_task, return_exceptions=True)
            self._force_finalize_task = None
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

    @property
    def keyterms(self) -> list[str] | None:
        """Current keyterm list (read by `connect()`)."""
        return self._keyterms

    def set_keyterms(self, keyterms: list[str] | None) -> None:
        """Update the keyterm list before `connect()` is called.

        Lets a caller (e.g. auto-preload) merge new dictionary terms in
        after construction but before the Deepgram connection opens, since
        keyterms are only read at `connect()` time.
        """
        self._keyterms = keyterms

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
                # Deepgram python SDK 3+ automatically handles keepalives.
                # await self._connection.send_control({"type": "KeepAlive"})
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._on_error(exc)

    async def _force_finalize_loop(self) -> None:
        """Periodically soft-finalize a stalled in-progress utterance.

        Continuous fluent speech can run well past `endpointing` /
        `utterance_end_ms` without Deepgram ever emitting `is_final` (no
        long-enough pause occurs). Without this, translation would silently
        stall for the entire stretch. Checked on a short interval so the
        forced cut lands close to `max_interim_duration` regardless of when
        Deepgram's own messages happen to arrive.

        Also the home of the optional confidence-early-commit check
        (h-asr-confidence-early-commit) and semantic-completeness-gating
        check (h-semantic-completeness-gating): reusing this same 0.5s
        periodic cadence (rather than checking on every interim message,
        the way LocalAgreement-2 does) is deliberate -- it moves the
        existing timer earlier for confidently-transcribed/semantically-
        complete speech without introducing a new high-frequency commit
        path.
        """
        try:
            while self._running:
                await asyncio.sleep(0.5)
                if not self._running or self._max_interim_duration is None:
                    continue
                if self._utterance_since is None or self._pending_result is None:
                    continue
                elapsed = time.monotonic() - self._utterance_since
                confident_early = (
                    self._confidence_early_commit_threshold is not None
                    and elapsed >= self._confidence_early_commit_min_elapsed
                    and self._pending_result.confidence
                    >= self._confidence_early_commit_threshold
                )
                semantic_early = False
                if (
                    not confident_early
                    and self._completeness_check is not None
                    and elapsed >= self._semantic_gating_min_elapsed
                    and elapsed < self._max_interim_duration
                    and (
                        self._last_semantic_check_at is None
                        or time.monotonic() - self._last_semantic_check_at
                        >= self._semantic_gating_check_interval
                    )
                ):
                    self._last_semantic_check_at = time.monotonic()
                    pending_text = self._pending_result.text
                    try:
                        is_complete = await self._completeness_check(pending_text)
                    except Exception:  # noqa: BLE001
                        is_complete = None
                    # The classifier call was awaited, so state may have
                    # moved on (a new message, or UtteranceEnd, arrived
                    # while we were waiting) -- re-check before acting on
                    # a now possibly-stale decision.
                    if (
                        is_complete
                        and self._utterance_since is not None
                        and self._pending_result is not None
                    ):
                        semantic_early = True
                should_finalize = (
                    elapsed >= self._max_interim_duration
                    or confident_early
                    or semantic_early
                )
                if should_finalize:
                    self._soft_finalize_pending(is_utterance_end=False)
                    # Deepgram's own utterance is still open; only restart
                    # the timeout window, don't reset utterance tracking.
                    self._utterance_since = time.monotonic()
                    self._last_semantic_check_at = None
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._on_error(exc)

    def _track_utterance_boundary(self, start: float) -> None:
        """Reset per-utterance consumption tracking when a new utterance starts."""
        if self._utterance_start_time is None or abs(
            start - self._utterance_start_time
        ) > 1e-6:
            self._utterance_start_time = start
            self._utterance_since = time.monotonic()
            self._consumed_word_count = 0
            self._consumed_end_time = start
            self._utterance_id += 1
            self._prev_interim_words = None
            self._last_semantic_check_at = None

    def _reset_utterance_state(self) -> None:
        self._utterance_start_time = None
        self._utterance_since = None
        self._consumed_word_count = 0
        self._consumed_end_time = None
        self._pending_result = None
        self._last_semantic_check_at = None
        self._prev_interim_words = None

    def _soft_finalize_pending(self, *, is_utterance_end: bool) -> None:
        """Emit unconsumed words from the current pending interim as final.

        Used by both the max-duration safety net (`is_utterance_end=False`:
        more chunks for this utterance are still expected) and `UtteranceEnd`
        handling (`is_utterance_end=True`: Deepgram considers the utterance
        over). Only emits the words not already consumed by a prior
        soft-finalization, since Deepgram repeats the full cumulative
        transcript on every message for an utterance, not just new words.
        """
        if self._pending_result is None:
            return
        words = self._pending_result.text.split()
        new_words = words[self._consumed_word_count :]
        if not new_words:
            return
        new_text = " ".join(new_words)
        start = (
            self._consumed_end_time
            if self._consumed_end_time is not None
            else self._pending_result.start_time
        )
        chunk = TranscriptionResult(
            text=new_text,
            is_final=True,
            confidence=self._pending_result.confidence,
            start_time=start,
            end_time=self._pending_result.end_time,
            utterance_id=self._utterance_id,
            is_utterance_end=is_utterance_end,
        )
        self._consumed_word_count = len(words)
        self._consumed_end_time = self._pending_result.end_time
        self._emit_result(chunk)

    def _maybe_commit_local_agreement(
        self, current_words: list[str], end_time: float
    ) -> None:
        """LocalAgreement-2 (h-localagreement-asr-commit): commit an agreed prefix.

        Compares this interim's words against the immediately preceding
        interim's words for the same utterance. Any prefix the two agree on
        beyond what's already been committed is safe to translate now,
        rather than waiting for `max_interim_duration`'s fixed timer --
        Deepgram is unlikely to revise a word once two consecutive updates
        have already agreed on it. Only ever extends `_consumed_word_count`
        forward, sharing that ledger with `_soft_finalize_pending` so the
        two commit paths never double-emit the same words.
        """
        prev_words = self._prev_interim_words
        self._prev_interim_words = current_words
        if not prev_words:
            return

        agree_up_to = 0
        limit = min(len(prev_words), len(current_words))
        while (
            agree_up_to < limit
            and prev_words[agree_up_to] == current_words[agree_up_to]
        ):
            agree_up_to += 1

        if agree_up_to <= self._consumed_word_count:
            return

        new_words = current_words[self._consumed_word_count : agree_up_to]
        if not new_words:
            return

        start = (
            self._consumed_end_time
            if self._consumed_end_time is not None
            else self._utterance_start_time
        )
        chunk = TranscriptionResult(
            text=" ".join(new_words),
            is_final=True,
            confidence=float(self._pending_result.confidence)
            if self._pending_result is not None
            else 1.0,
            start_time=start if start is not None else end_time,
            end_time=end_time,
            utterance_id=self._utterance_id,
            is_utterance_end=False,
        )
        self._consumed_word_count = agree_up_to
        self._consumed_end_time = end_time
        self._emit_result(chunk)

    def _handle_message(self, result: Any) -> None:
        """Handle transcription message from Deepgram.

        Args:
            result: Deepgram transcription result
        """
        try:
            message_type = getattr(result, "type", None)
            if message_type == "UtteranceEnd":
                # Flush any words not yet consumed by a natural `is_final`
                # or a prior soft-finalization, then reset: Deepgram
                # considers this utterance over.
                self._soft_finalize_pending(is_utterance_end=True)
                self._reset_utterance_state()
                return
            if message_type != "Results":
                return

            channel = result.channel
            alternative = channel.alternatives[0]
            transcript = alternative.transcript
            is_final = bool(getattr(result, "is_final", False))

            if not transcript:
                return

            start = float(result.start)
            end = float(result.start + result.duration)
            self._track_utterance_boundary(start)

            if is_final:
                words = transcript.split()
                new_words = words[self._consumed_word_count :]
                if new_words:
                    chunk_start = (
                        self._consumed_end_time
                        if self._consumed_end_time is not None
                        else start
                    )
                    final_result = TranscriptionResult(
                        text=" ".join(new_words),
                        is_final=True,
                        confidence=float(alternative.confidence),
                        start_time=chunk_start,
                        end_time=end,
                        utterance_id=self._utterance_id,
                        is_utterance_end=True,
                    )
                    self._emit_result(final_result)
                self._reset_utterance_state()
                return

            transcript_result = TranscriptionResult(
                text=transcript,
                is_final=False,
                confidence=float(alternative.confidence),
                start_time=self._utterance_start_time
                if self._utterance_start_time is not None
                else start,
                end_time=end,
                utterance_id=self._utterance_id,
                is_utterance_end=False,
            )
            self._pending_result = transcript_result
            if self._emit_interim:
                self._emit_result(transcript_result)
            if self._local_agreement_commit:
                self._maybe_commit_local_agreement(transcript.split(), end)

        except (AttributeError, IndexError):
            pass  # Ignore malformed results

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

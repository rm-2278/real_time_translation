"""Translation pipeline for real-time audio translation."""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from real_time_translation.audio.capture import AudioCapture
from real_time_translation.config import Config
from real_time_translation.preload.auto_preload import preload_translator
from real_time_translation.transcription.deepgram_client import (
    DeepgramTranscriber,
    TranscriptionResult,
)
from real_time_translation.translation.llm_translator import LLMTranslator
from real_time_translation.translation.rate_limiter import RateLimiter


@dataclass
class TranslationResult:
    """Complete (or in-progress) translation result.

    `translated_text` is cumulative: while a translation is streaming in,
    consumers receive successive results with growing text and
    `is_translation_complete=False`, then one final result with the full
    text and `is_translation_complete=True`. Consumers that only care about
    finished translations should filter on `is_final and is_translation_complete`.
    """

    original_text: str
    translated_text: str
    is_final: bool
    confidence: float
    is_translation_complete: bool = True
    start_time: float | None = None
    end_time: float | None = None
    kept_terms: list[str] = field(default_factory=list)
    slide_window: list[str] = field(default_factory=list)
    # False for a soft-finalized mid-utterance chunk (see
    # `deepgram_max_interim_duration`): more text for the same utterance is
    # still coming, `translated_text` is the utterance's accumulated
    # translation so far, and consumers should keep updating the *current*
    # displayed line rather than committing a new one. True (the default)
    # means this is the real end of the utterance -- safe to commit as a
    # finished line and start fresh for the next one.
    is_utterance_end: bool = True
    # Stable grouping key shared by every result belonging to the same
    # spoken utterance, including across separate (non-coalesced)
    # translation calls -- unlike `start_time`, which differs per call.
    # Consumers that need to associate a continuation result with its
    # predecessor (rather than relying on `translated_text` already being
    # pipeline-accumulated) should key on this instead.
    utterance_id: int = 0


@dataclass
class QueuedTranscription:
    """Queued transcription with masked text when needed."""

    original: TranscriptionResult
    text_for_translation: str
    context: list[str]
    queued_at: float = field(default_factory=time.time)


class _FragmentQueue:
    """Bounded FIFO queue of `QueuedTranscription`, with peek support.

    Deliberately not `asyncio.Queue`: batch draining (see
    `TranslationPipeline._drain_batch`) needs to look at the next item and
    decide *without removing it* whether it fits the current batch's
    character budget. `asyncio.Queue` has no peek, and popping-then-maybe-
    pushing-back would reorder it behind items queued after it (`put_nowait`
    only appends), silently breaking chronological order. `put` is the only
    method that adds items and wakes waiters, so there's a single code path
    to reason about for wakeups (no lost-wakeup risk from a second entry
    point mutating the deque).
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(1, maxsize)
        self._items: deque[QueuedTranscription] = deque()
        self._waiters: deque[asyncio.Future[None]] = deque()

    def put(self, item: QueuedTranscription) -> None:
        """Append, dropping the oldest not-yet-drained item if full."""
        if len(self._items) >= self._maxsize:
            self._items.popleft()
        self._items.append(item)
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                break

    async def get(self) -> QueuedTranscription:
        """Block until at least one item is available, then pop the front."""
        while not self._items:
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            except asyncio.CancelledError:
                waiter.cancel()
                raise
        return self._items.popleft()

    def peek(self) -> QueuedTranscription | None:
        """Look at the front item without removing it."""
        return self._items[0] if self._items else None

    def pop_peeked(self) -> QueuedTranscription:
        """Remove the item just returned by `peek()`."""
        return self._items.popleft()


class TranslationPipeline:
    """Pipeline for real-time audio translation.

    Coordinates audio capture, transcription, and translation.
    """

    def __init__(
        self,
        config: Config,
        audio_capture: AudioCapture,
    ) -> None:
        """Initialize translation pipeline.

        Args:
            config: Application configuration
            audio_capture: Audio capture instance
        """
        self._config = config
        self._audio_capture = audio_capture

        # Initialize translator first: it loads the terminology dictionary,
        # which the transcriber below also needs (as Deepgram keyterms) so
        # domain terms are ASR-corrected at the source instead of only
        # being patched up later by the LLM.
        api_key = (
            config.google_api_key
            if config.llm_provider == "gemini"
            else config.openai_api_key
        )
        model = (
            config.gemini_model
            if config.llm_provider == "gemini"
            else config.openai_model
        )

        self._translator = LLMTranslator(
            provider=config.llm_provider,  # type: ignore
            api_key=api_key or "",
            model=model,
            source_language=self._language_name(config.source_language),
            target_language=self._language_name(config.target_language),
            dictionary_path=config.dictionary_path,
            context_window_size=config.context_window_size,
            thinking_budget=config.gemini_thinking_budget,
            dictionary_dynamic_threshold=config.dictionary_dynamic_threshold,
            dictionary_dynamic_limit=config.dictionary_dynamic_limit,
            domain_packs=config.domain_packs,
            domain_packs_dir=config.domain_packs_dir,
        )

        keyterms = (
            self._translator.dictionary.source_terms(
                limit=config.deepgram_max_keyterms
            )
            if config.deepgram_keyterms_enabled
            else None
        )

        # Initialize transcriber
        self._transcriber = DeepgramTranscriber(
            api_key=config.deepgram_api_key,
            language=config.deepgram_language,
            model=config.deepgram_model,
            interim_results=config.deepgram_interim_results,
            smart_format=config.deepgram_smart_format,
            endpointing=config.deepgram_endpointing,
            utterance_end_ms=config.deepgram_utterance_end_ms,
            vad_events=config.deepgram_vad_events,
            emit_interim=True,  # Emit interim results for real-time UI
            max_interim_duration=config.deepgram_max_interim_duration,
            keyterms=keyterms,
            local_agreement_commit=config.localagreement_commit_enabled,
        )

        # Rate limiter shared across all translation workers, to stay under
        # the provider's RPM quota instead of firing requests that 429.
        self._translation_rate_limiter = RateLimiter(rate=config.gemini_rpm_limit)
        self._num_translation_workers = max(1, config.translation_workers)
        self._translation_timeout = config.translation_timeout
        self._translation_batch_max_items = max(1, config.translation_batch_max_items)
        self._translation_batch_max_chars = max(
            1, config.translation_batch_max_chars
        )

        # Concurrent workers finish translation calls in whatever order the
        # provider happens to respond, not the order utterances were spoken
        # in. `_emit_lock` serializes both the shared context/accumulator
        # mutation *and* delivery to `_on_result` so consumers only ever see
        # results in speech order, regardless of which worker finished
        # first. `_next_batch_id`/`_next_emit_batch_id` implement a small
        # reorder buffer: a batch that finishes early is held in
        # `_pending_batches` until every earlier batch has been emitted.
        self._emit_lock = asyncio.Lock()
        self._next_batch_id = 0
        self._next_emit_batch_id = 0
        self._pending_batches: dict[int, tuple[list[QueuedTranscription], str | None]] = {}

        # Utterances split across multiple soft-finalized translation calls
        # (see TranslationResult.is_utterance_end) are RE-translated from
        # scratch each time with the full text heard so far, not just the
        # new delta -- gluing together independently-translated fragments
        # produces grammatically incoherent output (each fragment was
        # translated blind, with no idea what would come next), whereas
        # giving the model the whole growing utterance each time lets it
        # produce one coherent sentence, self-correcting earlier word
        # choices as later context arrives (the same "re-translation"
        # strategy production simultaneous-translation systems use).
        # A continuation batch MUST NOT start translating until the prior
        # batch for the same utterance has updated this state -- see
        # `_get_utterance_lock` -- otherwise two continuations could race
        # and one would retranslate from stale/incomplete prior text.
        # Keyed by TranscriptionResult.utterance_id; entries are removed
        # once `is_utterance_end` commits the utterance (see
        # `_emit_batch_result`), so this doesn't grow unboundedly.
        self._utterance_target_text: dict[int, str] = {}  # text fed to the model
        self._utterance_source_text: dict[int, str] = {}  # raw ASR text, for display
        # h-continuation-context-anchor: this utterance's own most-recently-
        # emitted translation, for the next continuation batch to anchor to.
        # Same lifecycle as `_utterance_target_text`/`_utterance_source_text`.
        self._utterance_translated_text: dict[int, str] = {}
        self._utterance_locks: dict[int, asyncio.Lock] = {}

        self._running = False
        self._on_result: Callable[[TranslationResult], None] | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._transcription_queue = _FragmentQueue(maxsize=config.translation_queue_size)

    @staticmethod
    def _language_name(code: str) -> str:
        """Convert language code to language name.

        Args:
            code: Language code (e.g., "en", "ja")

        Returns:
            Language name
        """
        names = {
            "en": "English",
            "ja": "Japanese",
            "zh": "Chinese",
            "ko": "Korean",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
        }
        return names.get(code, code)

    def set_callback(self, callback: Callable[[TranslationResult], None]) -> None:
        """Set callback for translation results.

        Args:
            callback: Function to call with results
        """
        self._on_result = callback

    async def _auto_preload(self) -> None:
        """Merge supplementary terminology from `config.preload_source`.

        Runs before `prepare()`/`connect()` so a freshly-created Gemini
        cache and Deepgram's keyterm list both include any newly-merged
        terms from the start, rather than only from the next session. A
        no-op if `preload_source` isn't configured.
        """
        if self._config.preload_source is None:
            return

        added = await preload_translator(
            self._translator,
            self._config.preload_source,
            config=self._config,
            max_terms=self._config.preload_max_terms,
        )
        if added and self._config.deepgram_keyterms_enabled:
            self._transcriber.set_keyterms(
                self._translator.dictionary.source_terms(
                    limit=self._config.deepgram_max_keyterms
                )
            )

    async def start(self) -> None:
        """Start the translation pipeline."""
        self._running = True

        await self._auto_preload()

        # Initialize translator (e.g., Gemini context cache)
        await self._translator.prepare()

        # Connect to Deepgram
        await self._transcriber.connect()

        # Start audio capture
        await self._audio_capture.start()

        # Start processing tasks. Multiple parallel translation workers
        # prevent the sequential-processing lag-accumulation bug (see
        # CHANGELOG_2026-02-04.md): with one worker, a ~1s-per-Gemini-call
        # latency against continuous speech causes the queue (and lag) to
        # grow without bound. A shared rate limiter keeps workers from
        # exceeding the provider's RPM quota.
        self._tasks = [
            asyncio.create_task(self._audio_to_transcription()),
            asyncio.create_task(self._collect_transcriptions()),
            *(
                asyncio.create_task(self._translation_worker(i))
                for i in range(self._num_translation_workers)
            ),
        ]

    async def stop(self) -> None:
        """Stop the translation pipeline."""
        # Signal tasks to stop first to prevent blocking
        self._running = False

        # Stop audio capture to signal no more audio
        await self._audio_capture.stop()

        # Finalize the transcriber (signal end of audio stream)
        await self._transcriber.finalize()

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Disconnect transcriber
        await self._transcriber.disconnect()

    async def _audio_to_transcription(self) -> None:
        """Send audio data to transcriber."""
        try:
            async for audio_chunk in self._audio_capture.stream():
                if not self._running:
                    break
                await self._transcriber.send_audio(audio_chunk)
        except asyncio.CancelledError:
            pass

    async def _collect_transcriptions(self) -> None:
        """Collect transcription results into a queue."""
        try:
            async for result in self._transcriber.results():
                if not self._running:
                    break

                text = result.text.strip()
                if not text:
                    continue

                # Emit interim results to UI (without translation)
                if not result.is_final:
                    if self._on_result:
                        interim_result = TranslationResult(
                            original_text=text,
                            translated_text="",
                            is_final=False,
                            confidence=result.confidence,
                            start_time=result.start_time,
                            end_time=result.end_time,
                        )
                        self._on_result(interim_result)
                    continue

                # Handle low confidence by masking for translation input
                masked_text = (
                    f"[uncertain: {text}]" if result.is_low_confidence else text
                )

                queued = QueuedTranscription(
                    original=result,
                    text_for_translation=masked_text,
                    context=self._translator.context_snapshot(),
                )
                self._transcription_queue.put(queued)
        except asyncio.CancelledError:
            pass

    async def _drain_batch(self) -> list[QueuedTranscription]:
        """Block for one queued transcription, then greedily grab more.

        Draining beyond the first item never `await`s, so no other worker
        can interleave and steal part of the batch -- the queue's FIFO
        order guarantees batches are formed in strictly increasing speech
        order across all workers (see `_next_batch_id` in `start()`'s
        docstring-adjacent comment on `_emit_lock`). Coalescing multiple
        queued fragments into one LLM call is what keeps request volume
        bounded when translation falls behind realtime (e.g. continuous
        speech soft-finalizing every `deepgram_max_interim_duration`
        seconds against a low `gemini_rpm_limit`), and gives the model a
        fuller, more coherent span of text instead of many disjoint
        mid-sentence fragments.
        """
        batch = [await self._transcription_queue.get()]
        total_chars = len(batch[0].text_for_translation)
        while len(batch) < self._translation_batch_max_items:
            item = self._transcription_queue.peek()
            if item is None:
                break
            if total_chars + len(item.text_for_translation) > (
                self._translation_batch_max_chars
            ):
                # Leave it at the front of the queue for the next batch --
                # a peek never removes it, so no reordering risk.
                break
            batch.append(self._transcription_queue.pop_peeked())
            total_chars += len(item.text_for_translation)
        return batch

    def _get_utterance_lock(self, utterance_id: int) -> asyncio.Lock:
        """Get-or-create the lock serializing translation calls for one utterance.

        Only ever contended when the SAME utterance has more than one batch
        in flight (i.e. a soft-finalized continuation arrived before the
        previous part finished translating) -- unrelated utterances never
        touch each other's lock, so this doesn't limit cross-utterance
        concurrency.
        """
        lock = self._utterance_locks.get(utterance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._utterance_locks[utterance_id] = lock
        return lock

    async def _stream_batch(
        self,
        batch_id: int,
        batch: list[QueuedTranscription],
        full_target_text: str,
        *,
        live: bool,
        prior_translation: str | None = None,
    ) -> str:
        """Stream a translation of `full_target_text`, optionally live.

        `full_target_text` is everything heard for this utterance so far
        (see `_translation_worker`), not just this batch's new fragments --
        the return value is therefore always the complete, coherent
        translation of the utterance up to this point, never a fragment to
        be glued onto a previous one.

        `live=False` (a continuation of an in-progress utterance) skips the
        `is_translation_complete=False` progress updates entirely: this
        call retranslates from scratch, so streaming its growing output
        would visibly blank the caption and regrow it instead of extending
        smoothly. Better to leave the previous (already coherent) text on
        screen unchanged and snap directly to the new coherent result once
        it's ready, than to show a mid-retranslation half-sentence.

        Even when `live=True`, updates are only forwarded to `_on_result`
        while this batch is the head of the emit order
        (`batch_id == self._next_emit_batch_id`); a batch that raced ahead
        of an earlier one still-in-flight accumulates silently and is
        delivered in one shot by `_finish_batch` once its turn comes, so
        viewers never see a later utterance's text race an earlier one's
        onto the screen.

        Returns the accumulated (stripped) translation text.
        """
        accumulated = ""
        async for chunk in self._translator.translate_stream(
            full_target_text,
            context_lines=batch[0].context,
            update_context=False,
            prior_translation=prior_translation,
        ):
            accumulated += chunk
            if live and self._on_result and batch_id == self._next_emit_batch_id:
                self._on_result(
                    TranslationResult(
                        original_text=" ".join(q.original.text for q in batch),
                        translated_text=accumulated,
                        is_final=True,
                        is_translation_complete=False,
                        confidence=min(q.original.confidence for q in batch),
                        start_time=batch[0].original.start_time,
                        end_time=batch[-1].original.end_time,
                        is_utterance_end=batch[-1].original.is_utterance_end,
                        utterance_id=batch[-1].original.utterance_id,
                    )
                )
        return accumulated.strip()

    async def _finish_batch(
        self, batch_id: int, batch: list[QueuedTranscription], translation: str | None
    ) -> None:
        """Record a finished (or failed) batch and emit everything now in order.

        `translation is None` marks a batch that was dropped after retries
        (see `_translation_worker`) -- it still occupies `batch_id`'s slot
        in the reorder buffer so later batches aren't stuck waiting on a
        result that will never arrive.
        """
        async with self._emit_lock:
            self._pending_batches[batch_id] = (batch, translation)
            while self._next_emit_batch_id in self._pending_batches:
                ready_batch, ready_translation = self._pending_batches.pop(
                    self._next_emit_batch_id
                )
                self._next_emit_batch_id += 1
                if ready_translation is not None:
                    self._emit_batch_result(ready_batch, ready_translation)

    def _emit_batch_result(
        self, batch: list[QueuedTranscription], translation: str
    ) -> None:
        """Notify, and commit to rolling context once the utterance ends.

        `translation` is already the complete, coherent text for the
        utterance up to this batch (see `_stream_batch`) -- nothing to glue
        together here anymore.
        """
        last = batch[-1].original
        utterance_id = last.utterance_id
        acc_source = self._utterance_source_text.get(utterance_id) or " ".join(
            q.original.text for q in batch
        )

        if last.is_utterance_end:
            # Whole utterance is done: commit ONE coherent (source,
            # translation) pair to the rolling context/slide_window --
            # never the per-fragment deltas -- and drop this utterance's
            # scratch state (bounds `_utterance_*` dict growth).
            full_target_text = self._utterance_target_text.pop(utterance_id, None)
            if full_target_text is None:
                full_target_text = " ".join(q.text_for_translation for q in batch)
            self._translator.commit_context(full_target_text, translation)
            self._utterance_source_text.pop(utterance_id, None)
            self._utterance_translated_text.pop(utterance_id, None)
            self._utterance_locks.pop(utterance_id, None)

        if self._on_result:
            self._on_result(
                TranslationResult(
                    original_text=acc_source,
                    translated_text=translation,
                    is_final=True,
                    is_translation_complete=True,
                    confidence=min(q.original.confidence for q in batch),
                    start_time=batch[0].original.start_time,
                    end_time=last.end_time,
                    slide_window=self._translator.slide_window,
                    is_utterance_end=last.is_utterance_end,
                    utterance_id=utterance_id,
                )
            )

    async def _translation_worker(self, worker_id: int) -> None:
        """Consume queued transcriptions and translate, streaming output.

        Runs as one of several concurrent workers (see `start()`). Each
        translation call is stateless (`update_context=False`, explicit
        `context_lines` snapshotted at enqueue time) so concurrent workers
        don't race on the translator's internal context buffer; completed
        batches are committed and delivered in speech order by
        `_finish_batch` regardless of which worker finishes first.

        A batch belonging to an utterance that's still in progress (see
        `_get_utterance_lock`) is retranslated together with everything
        heard so far for that utterance, so it must wait for the prior
        batch of the *same* utterance to finish and record its state
        first -- unrelated utterances are unaffected and keep translating
        fully concurrently.

        A hung provider call (observed in practice: `generate_content_stream`
        can stall indefinitely with no error) must never permanently strand
        a worker -- that silently stops the whole pool one hang at a time,
        which is worse than a dropped segment. Every attempt is bounded by
        `translation_timeout` (scaled up slightly for larger batches) and
        retried once before the segment is dropped.
        """
        try:
            while self._running:
                batch = await self._drain_batch()
                batch_id = self._next_batch_id
                self._next_batch_id += 1

                utterance_id = batch[-1].original.utterance_id
                utterance_lock = self._get_utterance_lock(utterance_id)

                translation: str | None = None
                async with utterance_lock:
                    prior_target = self._utterance_target_text.get(utterance_id, "")
                    new_text = " ".join(q.text_for_translation for q in batch)
                    full_target_text = (
                        f"{prior_target} {new_text}".strip()
                        if prior_target
                        else new_text
                    )
                    is_continuation = bool(prior_target)

                    # h-masking-holdback: hold back the last N words of the
                    # accumulated hypothesis from translation while the
                    # utterance is still in progress (more soft-finalized
                    # continuations may still arrive). `full_target_text`
                    # itself stays untruncated -- it's what gets stored as
                    # `_utterance_target_text` below, so held-back words are
                    # never lost, just deferred to a later round.
                    holdback = self._config.masking_holdback_words
                    is_last_of_utterance = batch[-1].original.is_utterance_end
                    text_to_translate = full_target_text
                    if holdback > 0 and not is_last_of_utterance:
                        words = full_target_text.split(" ")
                        if len(words) > holdback:
                            text_to_translate = " ".join(words[:-holdback])

                    # h-continuation-context-anchor: anchor a continuation
                    # batch's retranslation to this utterance's own
                    # most-recently-emitted translation, so the model has an
                    # explicit memory of what's already on screen instead of
                    # reconstructing it from <target> alone.
                    prior_translation = None
                    if self._config.anchor_continuation_translation and is_continuation:
                        prior_translation = self._utterance_translated_text.get(
                            utterance_id
                        )

                    await self._translation_rate_limiter.acquire()
                    timeout = self._translation_timeout + 2.0 * (len(batch) - 1)

                    for attempt in range(2):
                        try:
                            translation = await asyncio.wait_for(
                                self._stream_batch(
                                    batch_id,
                                    batch,
                                    text_to_translate,
                                    live=not is_continuation,
                                    prior_translation=prior_translation,
                                ),
                                timeout=timeout,
                            )
                            break
                        except TimeoutError:
                            print(
                                f"Translation worker {worker_id}: timed out after "
                                f"{timeout}s (attempt {attempt + 1}/2)"
                                f" on {full_target_text[:50]!r}"
                            )
                        except Exception as exc:  # noqa: BLE001
                            print(f"Translation worker {worker_id}: error: {exc}")
                            break

                    if translation is not None:
                        self._utterance_target_text[utterance_id] = full_target_text
                        prior_source = self._utterance_source_text.get(
                            utterance_id, ""
                        )
                        new_source = " ".join(q.original.text for q in batch)
                        self._utterance_source_text[utterance_id] = (
                            f"{prior_source} {new_source}".strip()
                            if prior_source
                            else new_source
                        )
                        if not is_last_of_utterance:
                            self._utterance_translated_text[utterance_id] = translation

                await self._finish_batch(batch_id, batch, translation)
        except asyncio.CancelledError:
            pass

    def clear_context(self) -> None:
        """Clear translation context buffer."""
        self._translator.clear_context()

    async def run(self) -> None:
        """Run the pipeline until stopped."""
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

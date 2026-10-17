"""Translation queue manager with lag detection and adaptive processing.

CHANGE LOG (2026-02-04):
========================
NEW FILE - Created to fix lag accumulation issue.

Problem: Sequential translation caused 10+ minute lag accumulation during lectures.
- Input rate: ~1 sentence/1.5s = 40 sentences/min
- Processing rate: ~1 sentence/4s = 15 sentences/min (Gemini TTFT)
- Deficit: 25 sentences/min accumulating in queue

Solution: Parallel worker pool processes multiple translations concurrently.
- 6 workers × 15/min = 90/min capacity
- Queue stays at 0-1 items instead of growing indefinitely

Key components:
- TranslationQueueConfig: Configuration for workers, queue size, lag thresholds
- TranslationQueueManager: Manages async worker pool and shared queue
- Workers pull from shared asyncio.Queue and process independently
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TranslationRequest:
    """A queued translation request.
    
    Holds all data needed to process one translation job.
    """

    text: str  # The source text to translate
    context: list[str]  # Previous translations for context
    is_final: bool  # True if this is a final ASR result (not interim)
    ts: float  # Original timestamp when speech was captured
    session_id: str | None = None  # Optional session identifier
    queued_at: float = field(default_factory=time.time)  # When request entered queue


@dataclass
class TranslationQueueConfig:
    """Configuration for the translation queue.
    
    These settings control parallel processing and lag handling behavior.
    """

    # NEW 2026-02-04: Number of parallel workers - key to preventing lag accumulation
    # With 6 workers and 4s Gemini TTFT: 6 * 15/min = 90 translations/min capacity
    num_workers: int = 4
    # Maximum queue size - requests beyond this are dropped to prevent memory issues
    max_queue_size: int = 50
    # Lag threshold - requests older than this may be skipped or summarized
    lag_threshold_seconds: float = 5.0
    # Minimum texts to accumulate before summarizing (for lag recovery)
    min_texts_for_summary: int = 3
    # Maximum texts to accumulate before forcing summarization
    max_texts_for_summary: int = 8


class TranslationQueueManager:
    """Manages translation requests with parallel processing and lag handling.
    
    NEW 2026-02-04: This class is the core fix for lag accumulation.
    
    Architecture:
        [Incoming Requests] → [asyncio.Queue] → [Worker 0]
                                             → [Worker 1]
                                             → [Worker 2]
                                             → ...
                                             → [Worker N]
    
    Each worker independently pulls from the shared queue and processes
    translations concurrently, preventing sequential bottleneck.
    """

    def __init__(
        self,
        config: TranslationQueueConfig,
        # Callback function to perform actual translation (injected from main.py)
        translate_fn: Callable[
            [str, list[str], bool, float, str | None], Coroutine[Any, Any, None]
        ],
        # Optional callback to summarize multiple texts when lag is high
        summarize_fn: Callable[[list[str]], Coroutine[Any, Any, str]] | None = None,
    ) -> None:
        """Initialize the queue manager."""
        self._config = config
        self._translate_fn = translate_fn  # The actual translation function
        self._summarize_fn = summarize_fn  # For summarizing backlogged texts
        
        # Shared queue - all workers pull from this
        # maxsize prevents unbounded memory growth
        self._queue: asyncio.Queue[TranslationRequest] = asyncio.Queue(
            maxsize=config.max_queue_size
        )
        
        self._workers: list[asyncio.Task[None]] = []  # List of worker tasks
        self._running = False  # Flag to control worker lifecycle
        
        # Statistics for monitoring - exposed via /stats endpoint
        self._stats = {
            "processed": 0,  # Successfully translated
            "dropped": 0,    # Dropped due to queue full or too old
            "summarized": 0, # Summarized due to lag
            "current_lag": 0.0,  # Current lag in seconds
        }
        
        # Batch accumulator for summarization (when lag is high)
        self._batch: list[TranslationRequest] = []
        self._batch_lock = asyncio.Lock()  # Protects batch from concurrent access
        self._flush_task: asyncio.Task[None] | None = None

    @property
    def stats(self) -> dict[str, Any]:
        """Get current queue statistics for monitoring."""
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),  # Current items waiting
            "pending_for_summary": len(self._batch),  # Items waiting for summarization
        }

    async def start(self) -> None:
        """Start the worker tasks.
        
        NEW 2026-02-04: Creates N parallel workers, each running independently.
        This is the key to preventing lag accumulation.
        """
        if self._running:
            return
        self._running = True
        
        # Create N worker tasks - each runs concurrently
        for i in range(self._config.num_workers):
            task = asyncio.create_task(self._worker(i))  # Start worker coroutine
            self._workers.append(task)
        
        # Start periodic batch flush for summarization
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info("Started %d translation workers", self._config.num_workers)

    async def stop(self) -> None:
        """Stop the worker tasks gracefully."""
        self._running = False
        
        # Cancel flush task
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all workers
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        # Flush any remaining batch
        await self._flush_batch()
        logger.info("Stopped translation workers")

    async def enqueue(self, request: TranslationRequest) -> bool:
        """Add a translation request to the queue.
        
        Non-blocking: returns immediately whether queued or dropped.
        Called from /translate endpoint for each incoming ASR result.
        """
        try:
            # put_nowait avoids blocking the HTTP handler
            self._queue.put_nowait(request)
            return True
        except asyncio.QueueFull:
            # Queue is at max_queue_size - drop to prevent memory growth
            self._stats["dropped"] += 1
            logger.warning("Translation queue full, dropping request")
            return False

    async def _worker(self, worker_id: int) -> None:
        """Worker that processes translation requests from the shared queue.
        
        NEW 2026-02-04: Each worker runs in parallel, pulling from the same queue.
        This concurrency is what prevents lag accumulation.
        
        Worker loop:
            1. Wait for item from queue (with timeout to check _running flag)
            2. Process the translation request
            3. Mark task as done
            4. Repeat
        """
        logger.info("Translation worker %d started", worker_id)
        while self._running:
            try:
                # Wait for item with timeout (allows checking _running flag)
                request = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                # No item in queue, loop back to check _running
                continue
            except asyncio.CancelledError:
                # Worker being stopped
                break

            try:
                # Process the translation (may take ~4s due to Gemini TTFT)
                await self._process_request(request, worker_id)
            except Exception:
                logger.exception("Worker %d error processing request", worker_id)
            finally:
                # Always mark task done to keep queue accounting correct
                self._queue.task_done()

    async def _process_request(
        self, request: TranslationRequest, worker_id: int
    ) -> None:
        """Process a single translation request.
        
        Handles three cases:
        1. Recent interim (non-final) - translate immediately
        2. Old interim - drop (stale data)
        3. Final result - translate or batch for summarization if too old
        """
        current_time = time.time()
        lag = current_time - request.ts  # How old is this speech?
        self._stats["current_lag"] = lag

        # Case 1 & 2: Non-final (interim) ASR results
        if not request.is_final:
            if lag <= self._config.lag_threshold_seconds:
                # Recent interim - translate for quick feedback
                await self._translate_fn(
                    request.text,
                    request.context,
                    request.is_final,
                    request.ts,
                    request.session_id,
                )
                self._stats["processed"] += 1
            else:
                # Stale interim - skip (final will come soon anyway)
                self._stats["dropped"] += 1
            return

        # Case 3a: Final result but too old - add to batch for summarization
        if lag > self._config.lag_threshold_seconds and self._summarize_fn:
            async with self._batch_lock:
                self._batch.append(request)
                batch_size = len(self._batch)
                logger.info(
                    "Worker %d: Added to batch (size=%d, lag=%.1fs)",
                    worker_id,
                    batch_size,
                    lag,
                )
                # Force flush if batch is full (prevents batch growing forever)
                if batch_size >= self._config.max_texts_for_summary:
                    await self._flush_batch()
            return

        # Case 3b: Recent final result - normal translation
        await self._translate_fn(
            request.text,
            request.context,
            request.is_final,
            request.ts,
            request.session_id,
        )
        self._stats["processed"] += 1

    async def _periodic_flush(self) -> None:
        """Periodically flush the batch if enough items have accumulated.
        
        Runs as a background task to ensure batched items don't wait forever.
        Checks every 2 seconds and flushes if min_texts_for_summary is reached.
        """
        while self._running:
            await asyncio.sleep(2.0)  # Check interval
            async with self._batch_lock:
                if len(self._batch) >= self._config.min_texts_for_summary:
                    logger.info(
                        "Periodic flush: %d items in batch", len(self._batch)
                    )
                    await self._flush_batch()

    async def _flush_batch(self) -> None:
        """Flush accumulated batch - summarize multiple texts into one translation.
        
        This is used when lag is high and individual translations would fall
        further behind. Instead, we summarize multiple texts and translate once.
        """
        if not self._batch:
            return

        # Take ownership of batch and reset
        batch = self._batch
        self._batch = []

        # Extract text from requests
        texts = [r.text for r in batch]
        latest = batch[-1]  # Use metadata from latest request

        logger.info(
            "Flushing batch: %d texts (oldest lag: %.1fs)",
            len(texts),
            time.time() - batch[0].ts,
        )

        try:
            if self._summarize_fn and len(texts) > 1:
                # Summarize multiple texts into one for translation
                summarized = await self._summarize_fn(texts)
                self._stats["summarized"] += len(texts)
            else:
                # Fallback: just use the latest text, drop others
                summarized = texts[-1] if texts else ""
                self._stats["dropped"] += len(texts) - 1

            if summarized:
                await self._translate_fn(
                    summarized,
                    latest.context,
                    True,
                    latest.ts,
                    latest.session_id,
                )
                self._stats["processed"] += 1

        except Exception:
            logger.exception("Failed to flush batch")
            # On failure, try to process just the latest
            try:
                await self._translate_fn(
                    latest.text,
                    latest.context,
                    latest.is_final,
                    latest.ts,
                    latest.session_id,
                )
                self._stats["processed"] += 1
                self._stats["dropped"] += len(texts) - 1
            except Exception:
                logger.exception("Failed to process latest after batch failure")
                self._stats["dropped"] += len(texts)

"""Bounded FIFO of transcription fragments waiting to be translated.

Sits between ASR (producer: `TranslationPipeline._collect_transcriptions`) and
the translation workers (consumers). Kept apart from `pipeline.py` because it
is a self-contained data structure with its own ordering invariants, testable
without any audio, ASR or LLM.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from real_time_translation.transcription.deepgram_client import TranscriptionResult


@dataclass
class QueuedTranscription:
    """Queued transcription with masked text when needed."""

    original: TranscriptionResult
    text_for_translation: str
    context: list[str]
    previous_translations: list[str] | None = None
    queued_at: float = field(default_factory=time.time)


class FragmentQueue:
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

    def __len__(self) -> int:
        """Current backlog depth (h-backlog-adaptive-compression-budget)."""
        return len(self._items)

    def pop_peeked(self) -> QueuedTranscription:
        """Remove the item just returned by `peek()`."""
        return self._items.popleft()

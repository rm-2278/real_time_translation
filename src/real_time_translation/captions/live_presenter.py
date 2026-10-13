"""Real-time caption pacing: the live counterpart of `format_readable`.

The 2026-09-24 human review of the full LLM2024_8_part1 captions
(experiments/20260924_llm2024_8_part1_human_review.md) reported captions
that flash by ("ちょくちょく早いところがある（たまってまとめて）"), tiny
fragment cards ("そして", "私の"), and bad line breaks. The live overlay used
to replace its text with every translation result the instant it arrived,
so a backlog flush of several results showed each for a few milliseconds.

`LiveCaptionPresenter` applies the same rules as the offline formatter, in
real time:

- one card per (chunk of an) utterance, wrapped with the shared Latin-aware
  `_wrap_lines`/`_split_chunks` (no "Gemm|a" breaks, no line starting with
  "、");
- a tiny card (<= `tiny_chars`) is held briefly and folded into the next
  one if it arrives within `tiny_merge_window` seconds;
- each card is held for its reading-time budget, compressed toward
  `min_duration` as the queue falls behind (`schedule_adaptive`'s rule), so
  lag stays bounded but no card is ever shown for less than `min_duration`.

It is clock-agnostic: callers pass `now` (e.g. `time.monotonic()`) to
`push`/`poll`, which keeps it deterministic under test.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from real_time_translation.captions.subtitle_formatter import (
    _normalize_spaces,
    _split_chunks,
    _wrap_lines,
    weighted_len,
)


@dataclass(frozen=True)
class LiveCard:
    text: str  # may contain \n (pre-wrapped lines)
    shown_at: float
    duration: float
    lag: float  # shown_at - when its text was ready


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


class LiveCaptionPresenter:
    # Defaults differ from format_readable's Netflix TV values (13 chars,
    # 5/6s) on purpose. The overlay line is ~29 full-width chars wide, and
    # replaying the 2026-09-18 run's 07:30-21:50 events through this
    # presenter (caption_issue_metrics.live_overlay_metrics) gave:
    # 13ch/0.83s -> 17.7% of cards on screen <1s, mean lag 2.01s;
    # 20ch/1.0s -> 0% <1s, mean lag 1.91s.
    def __init__(
        self,
        *,
        max_chars_per_line: int = 20,
        max_lines: int = 2,
        min_duration: float = 1.0,
        max_duration: float = 7.0,
        reading_units_per_sec: float = 4.0,
        catchup_window: float = 4.0,
        tiny_chars: int = 4,
        tiny_merge_window: float = 2.0,
    ) -> None:
        self._max_chars_per_line = max_chars_per_line
        self._max_lines = max_lines
        self._min_duration = min_duration
        self._max_duration = max_duration
        self._reading_units_per_sec = reading_units_per_sec
        self._catchup_window = catchup_window
        self._tiny_chars = tiny_chars
        self._tiny_merge_window = tiny_merge_window

        # (ready_at, wrapped_text, ideal_duration)
        self._queue: deque[tuple[float, str, float]] = deque()
        self._current_end = float("-inf")
        self._pending_tiny: tuple[float, str] | None = None
        # Text already queued per still-open utterance, so a later, longer
        # translation of the same utterance only adds its new tail.
        self._queued_text: dict[int, str] = {}

    # -- input -------------------------------------------------------------

    def push(
        self,
        text: str,
        now: float,
        *,
        utterance_id: int | None = None,
        utterance_end: bool = True,
    ) -> None:
        """Add a completed translation (whole utterance so far, or a final)."""
        text = _normalize_spaces(text)
        if utterance_id is not None:
            prev = self._queued_text.get(utterance_id, "")
            if utterance_end:
                self._queued_text.pop(utterance_id, None)
            else:
                self._queued_text[utterance_id] = text
            if prev:
                lcp = _common_prefix_len(prev, text)
                # Mostly the same wording as what's already queued/shown:
                # only its new tail is news. A substantial rewrite (the
                # utterance-final full retranslation reworded it) is shown
                # in full again rather than as a nonsensical fragment.
                if lcp >= len(prev) // 2:
                    text = text[lcp:].lstrip("、。 ")
        if not text:
            return

        if self._pending_tiny is not None:
            text = f"{self._pending_tiny[1]}{text}"
            self._pending_tiny = None
        if len(text) <= self._tiny_chars:
            self._pending_tiny = (now, text)
            return
        self._enqueue(text, now)

    def _enqueue(self, text: str, ready_at: float) -> None:
        chunks = _split_chunks(text, self._max_chars_per_line * self._max_lines)
        weights = [weighted_len(c) for c in chunks]
        total_weight = sum(weights) or 1.0
        total_budget = max(
            self._min_duration * len(chunks),
            total_weight / self._reading_units_per_sec,
        )
        for chunk, weight in zip(chunks, weights, strict=True):
            wrapped = "\n".join(
                _wrap_lines(chunk, self._max_chars_per_line, self._max_lines)
            )
            duration = total_budget * (weight / total_weight)
            duration = min(max(duration, self._min_duration), self._max_duration)
            self._queue.append((ready_at, wrapped, duration))

    # -- output ------------------------------------------------------------

    def poll(self, now: float) -> LiveCard | None:
        """The next card, if the current one has had its time; else None.

        Never returns more than one card per call: even if polled late,
        each card still gets shown (and held) on its own.
        """
        if (
            self._pending_tiny is not None
            and now - self._pending_tiny[0] >= self._tiny_merge_window
        ):
            ready_at, text = self._pending_tiny
            self._pending_tiny = None
            self._enqueue(text, ready_at)

        if not self._queue or now < self._current_end:
            return None
        ready_at, text, ideal = self._queue.popleft()
        lag = max(0.0, now - ready_at)
        factor = 1.0
        if self._catchup_window > 0:
            factor = max(0.0, 1.0 - lag / self._catchup_window)
        duration = self._min_duration + factor * (ideal - self._min_duration)
        duration = max(duration, self._min_duration)
        self._current_end = now + duration
        return LiveCard(text, now, duration, lag)

    def is_idle(self, now: float) -> bool:
        """Nothing queued or held and the current card's minimum time is up.

        Only then is it safe to show a still-streaming partial translation
        without it overwriting a card the viewer hasn't had time to read.
        """
        return (
            not self._queue
            and self._pending_tiny is None
            and (now >= self._current_end)
        )

    def next_wakeup(self, now: float) -> float:
        """Seconds until `poll` could next return something."""
        candidates = []
        if self._queue:
            candidates.append(max(0.0, self._current_end - now))
        if self._pending_tiny is not None:
            candidates.append(
                max(0.0, self._pending_tiny[0] + self._tiny_merge_window - now)
            )
        return min(candidates) if candidates else 0.25

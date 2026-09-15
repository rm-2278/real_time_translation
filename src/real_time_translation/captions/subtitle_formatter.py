"""Turn an experiment JSON's translation events into subtitle cues.

Two formatting modes, for side-by-side readability comparisons:

- `format_naive`: one cue per utterance, full accumulated text, unwrapped --
  what you'd see if you dumped the pipeline's raw per-utterance output onto
  the screen with no post-processing.
- `format_readable`: the same text, but line-wrapped to a max character
  count, split into multiple sequential cues when it would otherwise force
  too many lines, and given a minimum on-screen duration derived from a
  reading-speed budget (characters per second) rather than just however long
  the utterance took to speak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str  # may contain \n for multi-line cues


def extract_utterance_cues(events: list[dict]) -> list[Cue]:
    """One cue per spoken utterance: (first batch start, last batch end, final text).

    Mirrors flicker_metrics.group_translation_by_utterance's batch-chaining
    heuristic (results.events carries no utterance_id, only is_utterance_end,
    so utterance spans are inferred sequentially), but additionally tracks
    the *last* batch's end time -- group_translation_by_utterance only keeps
    the first batch's (start, end) key, which understates a multi-batch
    utterance's true end time and is unusable for subtitle timing.
    """
    batches: list[tuple[float, float, str, bool]] = []
    cur_start: float | None = None
    cur_end: float | None = None
    cur_texts: list[str] = []
    cur_ended = True

    def flush_batch() -> None:
        if cur_texts:
            batches.append((cur_start or 0.0, cur_end or 0.0, cur_texts[-1], cur_ended))

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        start = e.get("asr_start_time", 0.0)
        end = e.get("asr_end_time", 0.0)
        if cur_start is None or (start, end) != (cur_start, cur_end):
            flush_batch()
            cur_start, cur_end = start, end
            cur_texts = []
        cur_texts.append(e.get("text", ""))
        cur_ended = e.get("is_utterance_end", True)
    flush_batch()

    cues: list[Cue] = []
    span_start: float | None = None
    span_end = 0.0
    span_text = ""
    prev_ended = True
    for start, end, text, ended in batches:
        if prev_ended:
            if span_start is not None:
                cues.append(Cue(span_start, span_end, span_text))
            span_start = start
        span_end = end
        span_text = text
        prev_ended = ended
    if span_start is not None:
        cues.append(Cue(span_start, span_end, span_text))
    return cues


def format_naive(cues: list[Cue]) -> list[Cue]:
    """No formatting at all: exactly what's already extracted."""
    return list(cues)


# Prefer breaking after these characters (natural pause points in Japanese).
_BREAK_AFTER = "、。！？"


def _wrap_lines(text: str, max_chars_per_line: int) -> list[str]:
    """Greedy wrap, preferring to break right after punctuation."""
    if len(text) <= max_chars_per_line:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars_per_line:
        window = remaining[: max_chars_per_line + 1]
        break_at = None
        for i in range(len(window) - 1, 0, -1):
            if window[i - 1] in _BREAK_AFTER:
                break_at = i
                break
        if break_at is None:
            break_at = max_chars_per_line
        lines.append(remaining[:break_at])
        remaining = remaining[break_at:]
    if remaining:
        lines.append(remaining)
    return lines


def _split_chunks(text: str, max_chars_per_card: int) -> list[str]:
    """Split long text into <= max_chars_per_card chunks, preferring punctuation."""
    if len(text) <= max_chars_per_card:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars_per_card:
        window = remaining[: max_chars_per_card + 1]
        split_at = None
        for i in range(len(window) - 1, 0, -1):
            if window[i - 1] in _BREAK_AFTER:
                split_at = i
                break
        if split_at is None:
            split_at = max_chars_per_card
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks


def format_readable(
    cues: list[Cue],
    *,
    max_chars_per_line: int = 16,
    max_lines: int = 2,
    min_duration: float = 1.2,
    reading_cps: float = 6.5,
) -> list[Cue]:
    """Wrap, split, and re-time cues for actual on-screen readability.

    - Long utterances split into multiple sequential cards (equal time
      split across the utterance's own span) instead of one overlong block.
    - Each card wrapped to at most `max_lines` lines of `max_chars_per_line`.
    - Each card's minimum duration is `len(text) / reading_cps` seconds
      (capped so it never exceeds its own share of the utterance's real
      span -- this is a demo, not a system free to hold the screen after
      the speaker has moved on).
    """
    max_chars_per_card = max_chars_per_line * max_lines
    out: list[Cue] = []
    for cue in cues:
        text = re.sub(r"\s+", "", cue.text)
        if not text:
            continue
        chunks = _split_chunks(text, max_chars_per_card)
        span = max(cue.end - cue.start, 0.01)
        share = span / len(chunks)
        t = cue.start
        for chunk in chunks:
            wrapped = "\n".join(_wrap_lines(chunk, max_chars_per_line))
            if len(chunks) == 1:
                # Single card: free to use a reading-speed-derived duration,
                # but never past the utterance's own real end.
                duration = max(min_duration, min(len(chunk) / reading_cps, span))
            else:
                # Multiple cards share the span equally -- never let one
                # card's duration bleed into the next card's slot.
                duration = share
            out.append(Cue(t, t + duration, wrapped))
            t += share
    return out


def _srt_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    lines = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

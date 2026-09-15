"""Turn an experiment JSON's translation events into subtitle cues.

Two formatting modes, for side-by-side readability comparisons:

- `format_naive`: one cue per utterance, full accumulated text, unwrapped --
  what you'd see if you dumped the pipeline's raw per-utterance output onto
  the screen with no post-processing.
- `format_readable`: the same text, but line-wrapped to a max character
  count (avoiding breaking a line right before a particle, which reads
  badly in Japanese), split into multiple sequential cues when it would
  otherwise force too many lines, and given an on-screen duration derived
  from a reading-speed budget that weighs kanji as costlier to read than
  kana/punctuation, rather than a flat per-character rate.

Both modes time a cue by when its translation was actually READY
(`playback_offset` on the event that finished it), not by when the speaker
started talking -- the whole point of these demos is to be honest about
end-to-end latency, not to make translation look faster than it is.
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


@dataclass(frozen=True)
class _RawUtterance:
    ready_at: float  # playback_offset when the final text for this utterance arrived
    text: str


def extract_utterance_cues(events: list[dict]) -> list[_RawUtterance]:
    """One entry per spoken utterance: (real arrival time, final text).

    `results.events` carries no utterance_id (only is_utterance_end), so
    utterance spans are inferred sequentially by chaining batches until one
    ends the utterance -- same heuristic as
    flicker_metrics.group_translation_by_utterance. `ready_at` is the
    *last* batch's `playback_offset`: the true wall-clock moment (relative
    to clip start) the utterance's complete translation became available,
    which is what a viewer would actually see it appear at.
    """
    batches: list[tuple[float, str, bool]] = []
    cur_key: tuple[float, float] | None = None
    cur_texts: list[str] = []
    cur_offset = 0.0
    cur_ended = True

    def flush_batch() -> None:
        if cur_texts:
            batches.append((cur_offset, cur_texts[-1], cur_ended))

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if cur_key is None or key != cur_key:
            flush_batch()
            cur_key = key
            cur_texts = []
        cur_texts.append(e.get("text", ""))
        cur_offset = e.get("playback_offset", cur_offset)
        cur_ended = e.get("is_utterance_end", True)
    flush_batch()

    # Each utterance's final content is its LAST batch (later batches in the
    # same span already contain the whole utterance so far, per
    # `_stream_batch`'s docstring) -- so just watch for the batch that ends
    # the utterance. A trailing un-ended batch (recording cut off mid-
    # utterance) still gets emitted, using whatever text it has so far.
    out: list[_RawUtterance] = []
    last_offset = 0.0
    last_text = ""
    for offset, text, ended in batches:
        last_offset, last_text = offset, text
        if ended:
            out.append(_RawUtterance(last_offset, last_text))
    if batches and not batches[-1][2]:
        out.append(_RawUtterance(last_offset, last_text))
    return out


def format_naive(
    utterances: list[_RawUtterance], *, hold_seconds: float = 3.0
) -> list[Cue]:
    """No formatting at all: full text, shown from real arrival for a flat hold."""
    cues: list[Cue] = []
    for u in utterances:
        text = re.sub(r"\s+", "", u.text)
        if text:
            cues.append(Cue(u.ready_at, u.ready_at + hold_seconds, text))
    return _clip_to_next_start(cues)


# Prefer breaking after these characters (natural pause points in Japanese).
_BREAK_AFTER = "、。！？"
# Never START a new line/chunk with one of these -- a stranded particle
# reads worse than a slightly short previous line.
_PARTICLES = "はがをにでともへやのからまでよりねよわ"


def _reading_weight(ch: str) -> float:
    """Kanji take longer to read than kana or punctuation; weight accordingly."""
    code = ord(ch)
    if 0x4E00 <= code <= 0x9FFF:  # CJK Unified Ideographs
        return 2.0
    if 0x3040 <= code <= 0x30FF:  # hiragana + katakana
        return 1.0
    return 0.7


def _weighted_len(text: str) -> float:
    return sum(_reading_weight(c) for c in text)


def _adjust_break(text: str, break_at: int, min_first_len: int = 1) -> int:
    """Shift a candidate break point left if it would strand a particle."""
    while break_at > min_first_len and text[break_at] in _PARTICLES:
        break_at -= 1
    return break_at


def _wrap_lines(text: str, max_chars_per_line: int, max_lines: int = 2) -> list[str]:
    """Greedy wrap: prefer punctuation, else avoid stranding a particle.

    Shifting a break point left to keep a particle with its line (see
    `_adjust_break`) shortens that line below `max_chars_per_line`, which
    can leave more characters in `remaining` than a caller who sized its
    own budget as `max_chars_per_line * max_lines` accounted for. Once
    `max_lines - 1` breaks are placed, stop breaking and force everything
    left onto the final line -- a slightly long last line beats a line the
    renderer was never told about and silently drops.
    """
    if len(text) <= max_chars_per_line:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars_per_line and len(lines) < max_lines - 1:
        window = remaining[: max_chars_per_line + 1]
        break_at = None
        for i in range(len(window) - 1, 0, -1):
            if window[i - 1] in _BREAK_AFTER:
                break_at = i
                break
        if break_at is None:
            break_at = _adjust_break(remaining, max_chars_per_line)
        lines.append(remaining[:break_at])
        remaining = remaining[break_at:]
    if remaining:
        lines.append(remaining)
    return lines


# A card shorter than this is an "orphan" -- e.g. a lone trailing "、" left
# over when an utterance's own text happens to end mid-clause (translation
# not yet complete when this batch closed out) and the last split point
# lands right before the end. Flashing that alone for under a second is
# worse than a slightly-over-budget previous card -- merge it back in,
# the same trade-off `_adjust_break` already makes for stranded particles.
_ORPHAN_CHARS = 2


def _merge_orphan_chunks(chunks: list[str]) -> list[str]:
    if len(chunks) < 2:
        return chunks
    merged = chunks[:1]
    for chunk in chunks[1:]:
        if len(chunk) <= _ORPHAN_CHARS:
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)
    # An orphan as the very first chunk (nothing before it to merge into)
    # gets folded forward into what's now index 1 instead.
    if len(merged) >= 2 and len(merged[0]) <= _ORPHAN_CHARS:
        merged[1] = merged[0] + merged[1]
        merged = merged[1:]
    return merged


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
            split_at = _adjust_break(remaining, max_chars_per_card)
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return _merge_orphan_chunks(chunks)


# Netflix's minimum inter-subtitle gap is 2 frames; at a nominal 24fps
# that's this many seconds. Not exact for every frame rate, but this is a
# demo, not a broadcast QC pass -- close enough to follow the convention.
_MIN_GAP_SECONDS = 2 / 24


def _clip_to_next_start(cues: list[Cue]) -> list[Cue]:
    """Order by real arrival time, then never let a cue's end run past the next.

    Utterances are translated by a pool of concurrent workers (see
    pipeline.py's `_translation_worker`), so a later utterance's translation
    can genuinely finish before an earlier one's -- sort by actual arrival
    time first, since that's the order a viewer would really see cues
    appear in, not the order utterances were spoken in.
    """
    ordered = sorted(cues, key=lambda c: c.start)
    out: list[Cue] = []
    for i, cue in enumerate(ordered):
        end = cue.end
        if i + 1 < len(ordered):
            end = min(end, ordered[i + 1].start - _MIN_GAP_SECONDS)
        # A strictly-positive floor, not the usual ~1s minimum: two
        # utterances can genuinely finish translating a few milliseconds
        # apart, and a hard hold-time floor there would just recreate the
        # overlap we're clipping away. Sub-frame durations are effectively
        # invisible at any normal video frame rate anyway.
        end = max(end, cue.start + 0.001)
        out.append(Cue(cue.start, end, cue.text))
    return out


# Reading-speed and line-length defaults follow Netflix's published
# Japanese Timed Text Style Guide: 4 CPS, max 13 full-width characters per
# line, minimum display 5/6s, maximum display 7s. `reading_units_per_sec`
# is calibrated to that 4 CPS figure for pure-kana text (weight 1.0/char);
# kanji is weighted 2x (see `_reading_weight`), so a pure-kanji line reads
# at an effective 2 raw-characters/second, which is the intent of the
# style guide's own "kanji needs more room than kana" rationale, made
# explicit and computed rather than left to a human subtitler's judgment.
def _build_readable_items(
    utterances: list[_RawUtterance],
    *,
    max_chars_per_line: int,
    max_lines: int,
    min_duration: float,
    max_duration: float,
    reading_units_per_sec: float,
) -> list[tuple[float, str, float]]:
    """(ready_at, wrapped_text, duration) for every card, unscheduled."""
    max_chars_per_card = max_chars_per_line * max_lines
    items: list[tuple[float, str, float]] = []
    for u in utterances:
        text = re.sub(r"\s+", "", u.text)
        if not text:
            continue
        chunks = _split_chunks(text, max_chars_per_card)
        weights = [_weighted_len(c) for c in chunks]
        total_weight = sum(weights) or 1.0
        total_budget = max(
            min_duration * len(chunks), total_weight / reading_units_per_sec
        )
        for chunk, weight in zip(chunks, weights, strict=True):
            wrapped = "\n".join(_wrap_lines(chunk, max_chars_per_line, max_lines))
            duration = total_budget * (weight / total_weight)
            duration = min(max(duration, min_duration), max_duration)
            items.append((u.ready_at, wrapped, duration))
    items.sort(key=lambda item: item[0])
    return items


def schedule_with_lag(
    items: list[tuple[float, str, float]],
) -> list[tuple[Cue, float]]:
    """Hold-and-queue schedule; also reports each card's display lag.

    `lag = cue.start - ready_at`: how many seconds after its translation was
    actually ready a card had to wait for the previous card's reading time
    to finish before it could show. 0 when a card shows the instant it's
    ready (no queueing pressure at that point). This is the direct,
    measurable cost of guaranteeing every card its full reading time.

    Measured 2026-09-15 on 3 real recordings (see
    `captions/latency_diagnostics.py`, experiments/20260915_*.json): with
    Netflix's own max_duration=7.0 this schedule accumulates 45-90s of mean
    lag on a 90-120s clip -- a card holding its full ideal reading time
    with no regard for how far behind real time the queue already is will
    let backlog grow without bound whenever translations arrive faster
    than they can be comfortably read (concurrent workers finishing several
    utterances close together, or just a speaker not pausing). Simply
    lowering max_duration "fixes" the lag by breaking the reading-time
    guarantee for most cards instead (cps_violation_rate jumped from ~23%
    to ~85-94% across the same recordings at max_duration<=3.0). See
    `schedule_adaptive` for the actual fix.
    """
    out: list[tuple[Cue, float]] = []
    cursor = 0.0
    for ready_at, text, duration in items:
        start = max(ready_at, cursor)
        end = start + duration
        out.append((Cue(start, end, text), start - ready_at))
        cursor = end
    return out


def schedule_adaptive(
    items: list[tuple[float, str, float]],
    *,
    min_duration: float,
    catchup_window: float,
) -> list[tuple[Cue, float]]:
    """Hold-and-queue, but compress toward min_duration as backlog builds.

    `schedule_with_lag` guarantees every card its full ideal reading
    duration always, which turns into unbounded queueing lag under
    backlog (see its docstring for the measured numbers). Capping
    max_duration "fixes" the lag but breaks the reading-time guarantee for
    most cards, not just the backlogged ones -- it punishes a calm,
    well-paced stretch exactly as hard as a bursty one.

    This keeps the full guarantee when the schedule is caught up, and lets
    it degrade smoothly, in proportion to how far behind it actually is:
    a card still always gets at least `min_duration` (the hard floor both
    Netflix's and BBC's own guides use -- readability never fully
    collapses), but as this card's own lag approaches `catchup_window`
    seconds, its ideal duration is linearly compressed toward that floor.
    A backlog can then never grow past roughly `catchup_window`, and it
    actively drains during any quieter stretch, instead of ratcheting up
    for the rest of the clip the way a flat guarantee does.
    """
    out: list[tuple[Cue, float]] = []
    cursor = 0.0
    for ready_at, text, ideal_duration in items:
        start = max(ready_at, cursor)
        lag = start - ready_at
        factor = 1.0
        if catchup_window > 0:
            factor = max(0.0, 1.0 - lag / catchup_window)
        duration = min_duration + factor * (ideal_duration - min_duration)
        duration = max(duration, min_duration)
        end = start + duration
        out.append((Cue(start, end, text), lag))
        cursor = end
    return out


# Reading-speed and line-length defaults follow Netflix's published
# Japanese Timed Text Style Guide: 4 CPS, max 13 full-width characters per
# line, minimum display 5/6s, maximum display 7s. `reading_units_per_sec`
# is calibrated to that 4 CPS figure for pure-kana text (weight 1.0/char);
# kanji is weighted 2x (see `_reading_weight`), so a pure-kanji line reads
# at an effective 2 raw-characters/second, which is the intent of the
# style guide's own "kanji needs more room than kana" rationale, made
# explicit and computed rather than left to a human subtitler's judgment.
#
# max_duration stays at Netflix's true 7.0 -- it now only caps the IDEAL,
# caught-up-schedule duration a card computes for itself; `catchup_window`
# (see `schedule_adaptive`) is what actually keeps a live pipeline's
# queueing lag bounded, by compressing that ideal duration toward
# min_duration once a card is running behind.
#
# Honest reading of the 2026-09-15 sweep (3 real recordings, see
# `captions/latency_diagnostics.py --adaptive`): there is no setting that
# gets both low lag AND Netflix-grade cps_violation_rate on this kind of
# content -- a guest lecture translated live, dense with kanji technical
# terms, with utterances arriving close together, is close to the physical
# limit of what "read it comfortably AND show it promptly" can both mean
# at once. What catchup_window buys is a strictly *better trade* than
# flatly capping max_duration: at matched mean lag (~2.4-3.6s), the
# adaptive schedule's cps_violation_rate ran 8-14 points lower than the
# flat schedule's nearest max_duration setting across all 3 recordings
# (e.g. gemini_full: flat max_duration~2.4 interpolates to ~87%
# cps_violation at that lag; catchup_window=1.5 gets the same ~2.4s lag at
# 74.3%). catchup_window=4.0 is the default here as a practical middle of
# that curve, not a value that makes the trade-off disappear.
def format_readable(
    utterances: list[_RawUtterance],
    *,
    max_chars_per_line: int = 13,
    max_lines: int = 2,
    min_duration: float = 5 / 6,
    max_duration: float = 7.0,
    reading_units_per_sec: float = 4.0,
    catchup_window: float = 4.0,
) -> list[Cue]:
    """Wrap, split, and re-time cues for actual on-screen readability.

    - Each utterance's text is wrapped to at most `max_lines` lines of
      `max_chars_per_line`, breaking after punctuation and never stranding
      a lone particle at the start of a line.
    - Text too long for one card splits into multiple sequential cards; the
      display-time budget for a multi-card utterance is divided across
      cards by each card's own reading weight (kanji-heavy chunks get more
      time), not split evenly by count.
    - Every card gets at least `min_duration`, always. When the schedule is
      caught up it gets its full ideal reading duration; when translations
      have been arriving faster than they can be read, that ideal duration
      is adaptively compressed toward the floor so lag stays bounded
      instead of accumulating for the rest of the clip -- see
      `schedule_adaptive`. This is a deliberate middle ground between
      `format_naive` (no guarantee at all) and a flat hold-and-queue (full
      guarantee always, unbounded lag).
    """
    items = _build_readable_items(
        utterances,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        min_duration=min_duration,
        max_duration=max_duration,
        reading_units_per_sec=reading_units_per_sec,
    )
    scheduled = schedule_adaptive(
        items, min_duration=min_duration, catchup_window=catchup_window
    )
    return [cue for cue, _lag in scheduled]


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(seconds, 0.0) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: Path) -> None:
    lines = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

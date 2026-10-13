"""Normalized erasure (caption flicker/stability) scoring.

chrF and latency, the two metrics this project already tracks in
`experiments/results.csv`, both only look at the *final* output. Neither
says anything about how much the on-screen text was rewritten while the
viewer was reading it -- which is exactly the blind spot the simultaneous-
translation literature (Arivazhagan et al.'s re-translation papers; the
survey this module was commissioned from) calls out: offline metrics
completely ignore revision/flicker.

This module computes normalized erasure (NE) retroactively from the
timestamped event log every experiment JSON already contains under
`results.events` -- no new ASR/LLM calls are needed. Two independent NE
scores are computed, because this pipeline has two separate places where a
displayed hypothesis can be revised:

1. ASR-level NE (word-level, English): Deepgram interim results for the
   same in-progress utterance (grouped by their shared `asr_start_time`,
   which stays fixed while `asr_end_time` grows) sometimes don't just grow
   monotonically -- the recognizer can revise earlier words as more audio
   arrives. This is visible directly in the event log, e.g. one interim
   reading "this is after all a high level preview..." followed by the next
   interim for the *same* utterance reading "is after all a high level
   preview..." (the leading word was dropped/changed).
2. Translation-level NE (character-level, per the survey's guidance that
   Japanese has no whitespace so char-level is the right granularity):
   translation_partial/translation_complete events for the same committed
   ASR window (grouped by the `(asr_start_time, asr_end_time)` pair), which
   in this pipeline's chunked-commit mode are expected to grow monotonically
   as the LLM streams its answer (token-by-token append, not word
   substitution) -- so a non-zero score here would itself be a finding
   worth investigating, not just noise.

NE definition used here (reconstructed from the description in the survey
that commissioned this module, following Arivazhagan et al.): for an
ordered sequence of hypotheses h_1..h_n for the same growing utterance,
   erasure_i = len(h_{i-1}) - longest_common_prefix_len(h_{i-1}, h_i)
   NE = sum(erasure_i for i=2..n) / len(h_n)
NE=0 means the displayed prefix was never invalidated; higher NE means more
of what was on screen had to be taken back. This should be checked against
the primary source (not yet WebFetched at the time this module was written)
during a future READ_PAPERS pass -- see research_agent/state/papers.json,
entry `arivazhagan2019-erasure`.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from real_time_translation.experiments.stats import mean, median


def _longest_common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def normalized_erasure(hypotheses: list[str]) -> float | None:
    """NE over an ordered sequence of same-utterance hypotheses (already
    tokenized into whatever unit -- pass characters for char-level NE, or
    pre-split words joined back with a separator that participates in the
    prefix comparison for word-level NE). Returns None if there are fewer
    than 2 hypotheses (nothing to revise)."""
    if len(hypotheses) < 2:
        return None
    final_len = len(hypotheses[-1])
    if final_len == 0:
        return None
    total_erasure = 0
    for prev, cur in pairwise(hypotheses):
        lcp = _longest_common_prefix_len(prev, cur)
        total_erasure += len(prev) - lcp
    return total_erasure / final_len


def _word_units(text: str) -> str:
    """Join words with a sentinel separator so the char-level LCP logic
    above only matches on whole-word boundaries (word-level NE)."""
    return "\x1f".join(text.split())


@dataclass(frozen=True)
class UtteranceGroupStats:
    key: tuple[float, float]
    num_hypotheses: int
    ne: float | None
    final_text: str
    first_batch_duration: float | None = None


def group_asr_interim(events: list[dict]) -> list[UtteranceGroupStats]:
    groups: list[UtteranceGroupStats] = []
    current_start: float | None = None
    current: list[str] = []

    def flush() -> None:
        if current:
            hyps = [_word_units(t) for t in current]
            groups.append(
                UtteranceGroupStats(
                    key=(current_start or 0.0, 0.0),
                    num_hypotheses=len(current),
                    ne=normalized_erasure(hyps),
                    final_text=current[-1],
                )
            )

    for e in events:
        if e.get("kind") != "asr_interim":
            continue
        start = e.get("asr_start_time", 0.0)
        if current_start is None or start != current_start:
            flush()
            current_start = start
            current = []
        current.append(e.get("text", ""))
    flush()
    return groups


def group_translation(events: list[dict]) -> list[UtteranceGroupStats]:
    groups: list[UtteranceGroupStats] = []
    current_key: tuple[float, float] | None = None
    current: list[str] = []

    def flush() -> None:
        if current:
            groups.append(
                UtteranceGroupStats(
                    key=current_key or (0.0, 0.0),
                    num_hypotheses=len(current),
                    ne=normalized_erasure(current),
                    final_text=current[-1],
                )
            )

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if current_key is None or key != current_key:
            flush()
            current_key = key
            current = []
        current.append(e.get("text", ""))
    flush()
    return groups


def group_translation_by_utterance(events: list[dict]) -> list[UtteranceGroupStats]:
    """Cross-batch (across-continuation) translation NE.

    `group_translation` above groups by `(asr_start_time, asr_end_time)`, i.e.
    a single `_stream_batch()` call -- so it only ever sees token-by-token
    streaming within one call, which is append-only by construction and
    therefore always ~0. Per `_stream_batch`'s own docstring, a continuation
    batch for an in-progress (not yet utterance-ending) utterance
    re-translates the *entire* accumulated source text from scratch, so nothing
    guarantees a later batch's `full_target_text` keeps the same prefix as an
    earlier batch's -- that cross-batch rewriting is invisible to
    `group_translation`. This function groups instead by utterance span,
    reusing each batch's *last* (most complete) text, and computes NE across
    the ordered sequence of per-batch full texts within a span.

    `results.events`/`TimedEvent` carries no `utterance_id` field (only
    `is_utterance_end`, verified against video_segment.py's TimedEvent and an
    actual experiment JSON) -- so span boundaries here are a sequential
    heuristic: a new span starts immediately after any batch whose last event
    has `is_utterance_end=True`. This is valid for these single-speaker
    sequential-utterance experiments but is not a true utterance_id join --
    callers/reports should state that caveat explicitly.
    """
    # Step 1: collapse into per-batch (asr_start_time, asr_end_time) groups,
    # same key logic as group_translation, keeping each batch's last text and
    # its last event's is_utterance_end flag.
    batches: list[tuple[tuple[float, float], str, bool]] = []
    current_key: tuple[float, float] | None = None
    current_texts: list[str] = []
    current_end_flag = True

    def flush_batch() -> None:
        if current_texts:
            batches.append(
                (current_key or (0.0, 0.0), current_texts[-1], current_end_flag)
            )

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if current_key is None or key != current_key:
            flush_batch()
            current_key = key
            current_texts = []
        current_texts.append(e.get("text", ""))
        current_end_flag = e.get("is_utterance_end", True)
    flush_batch()

    # Step 2: chain consecutive batches into utterance spans, breaking right
    # after a batch that ended the utterance.
    groups: list[UtteranceGroupStats] = []
    span_key: tuple[float, float] | None = None
    span_texts: list[str] = []

    def flush_span() -> None:
        if span_texts:
            key = span_key or (0.0, 0.0)
            # Wall-clock duration of the span's *first* batch only (its own
            # asr_end_time - asr_start_time) -- i.e. how long the utterance
            # had been running before it first crossed into a continuation
            # batch. Only meaningful for multi-batch spans; a single-batch
            # span never "crossed" into anything.
            first_batch_duration = key[1] - key[0] if len(span_texts) >= 2 else None
            groups.append(
                UtteranceGroupStats(
                    key=key,
                    num_hypotheses=len(span_texts),
                    ne=normalized_erasure(span_texts),
                    final_text=span_texts[-1],
                    first_batch_duration=first_batch_duration,
                )
            )

    prev_ended = True
    for key, text, ended in batches:
        if prev_ended:
            flush_span()
            span_key = key
            span_texts = []
        span_texts.append(text)
        prev_ended = ended
    flush_span()
    return groups



def first_batch_durations(path: Path) -> list[float]:
    """Raw per-span first-batch wall-clock durations (seconds) for every
    multi-batch utterance span in one experiment JSON -- the same spans
    group_translation_by_utterance() finds, filtered to ones with >=2
    batches (a single-batch span has no "first batch duration" to report)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    spans = group_translation_by_utterance(events)
    return [
        s.first_batch_duration for s in spans if s.first_batch_duration is not None
    ]


@dataclass(frozen=True)
class FlickerReport:
    json_path: str
    experiment_name: str
    num_asr_utterance_groups: int
    asr_ne_word_mean: float | None
    num_translation_utterance_groups: int
    translation_ne_char_mean: float | None
    num_translation_utterance_spans: int
    translation_ne_char_cross_batch_mean: float | None
    num_multi_batch_spans: int
    first_batch_duration_mean: float | None


def analyze_experiment(path: Path) -> FlickerReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    asr_groups = group_asr_interim(events)
    tr_groups = group_translation(events)
    tr_spans = group_translation_by_utterance(events)
    asr_ne = [g.ne for g in asr_groups if g.ne is not None]
    tr_ne = [g.ne for g in tr_groups if g.ne is not None]
    tr_span_ne = [g.ne for g in tr_spans if g.ne is not None]
    first_batch_durations = [
        g.first_batch_duration for g in tr_spans if g.first_batch_duration is not None
    ]
    return FlickerReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_asr_utterance_groups=len(asr_groups),
        asr_ne_word_mean=mean(asr_ne),
        num_translation_utterance_groups=len(tr_groups),
        translation_ne_char_mean=mean(tr_ne),
        num_translation_utterance_spans=len(tr_spans),
        translation_ne_char_cross_batch_mean=mean(tr_span_ne),
        num_multi_batch_spans=len(first_batch_durations),
        first_batch_duration_mean=mean(first_batch_durations),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Experiment JSON file(s) or glob(s). Defaults to experiments/*.json",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/flicker_metrics.csv"),
        help="Sidecar CSV to write (kept separate from results.csv so this "
        "doesn't require rewriting/backfilling that file's existing schema)",
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        paths.extend(Path(p) for p in matched)
    # dedupe while preserving order
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[FlickerReport] = []
    all_first_batch_durations: list[float] = []
    for path in paths:
        try:
            reports.append(analyze_experiment(path))
            all_first_batch_durations.extend(first_batch_durations(path))
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_asr_utterance_groups",
                "asr_ne_word_mean",
                "num_translation_utterance_groups",
                "translation_ne_char_mean",
                "num_translation_utterance_spans",
                "translation_ne_char_cross_batch_mean",
                "num_multi_batch_spans",
                "first_batch_duration_mean",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_asr_utterance_groups,
                    r.asr_ne_word_mean,
                    r.num_translation_utterance_groups,
                    r.translation_ne_char_mean,
                    r.num_translation_utterance_spans,
                    r.translation_ne_char_cross_batch_mean,
                    r.num_multi_batch_spans,
                    r.first_batch_duration_mean,
                ]
            )

    print(f"analyzed {len(reports)} experiment(s) -> {args.out_csv}")
    scored = [r for r in reports if r.translation_ne_char_mean is not None]
    if scored:
        overall = mean([r.translation_ne_char_mean for r in scored])  # type: ignore[list-item]
        print(f"translation_ne_char_mean across corpus: {overall:.4f}")
    scored_asr = [r for r in reports if r.asr_ne_word_mean is not None]
    if scored_asr:
        overall_asr = mean([r.asr_ne_word_mean for r in scored_asr])  # type: ignore[list-item]
        print(f"asr_ne_word_mean across corpus: {overall_asr:.4f}")
    scored_cross = [
        r for r in reports if r.translation_ne_char_cross_batch_mean is not None
    ]
    if scored_cross:
        overall_cross = mean(
            [r.translation_ne_char_cross_batch_mean for r in scored_cross]  # type: ignore[list-item]
        )
        print(
            f"translation_ne_char_cross_batch_mean across corpus: {overall_cross:.4f}"
        )
    if all_first_batch_durations:
        print(
            f"first_batch_duration (s) across {len(all_first_batch_durations)} "
            f"multi-batch span(s): mean={mean(all_first_batch_durations):.3f} "
            f"median={median(all_first_batch_durations):.3f} "
            f"min={min(all_first_batch_durations):.3f} "
            f"max={max(all_first_batch_durations):.3f}"
        )


if __name__ == "__main__":
    main()

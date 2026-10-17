"""Time-to-first-visible-text, English interim vs. Japanese translation
(h-interim-source-preview-overlay, Step 1: $0 retroactive analysis only).

On `20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json` the hypothesis
observed (informally, not from a committed script) that the first
`asr_interim` event for an utterance arrives a median ~1.17s after that
utterance's `asr_start_time`, while the first translation text for the same
utterance arrives a median ~6.01s after it -- so a viewer sees nothing new
on screen for several seconds while the English interim was already
available. This module computes that pair of numbers properly, joined
per-utterance (not as two separately-computed distributions), from the
`wall_clock_lag` field every event in `results.events` already carries
(`playback_offset - asr_start_time`, see `video_segment.py`'s `TimedEvent`
docstring) -- no new ASR/LLM calls needed.

Per utterance (grouped by `asr_start_time`, same convention as
`flicker_metrics.group_asr_interim`):
  - `time_to_first_interim_s`: `wall_clock_lag` of the first `asr_interim`
    event in the group.
  - `time_to_first_translation_s`: `wall_clock_lag` of the first
    `translation_partial`/`translation_complete` event in the utterance's
    translation span (joined on the same `asr_start_time`, using
    `flicker_metrics.group_translation_by_utterance`'s span key, which is
    the first batch's `(asr_start_time, asr_end_time)` -- its `[0]` matches
    this group's `asr_start_time` because a translation span always starts
    with the batch covering the utterance's opening audio).
  - `preview_gain_s`: `time_to_first_translation_s - time_to_first_interim_s`
    -- how much earlier a viewer would see *something* if the English
    interim were shown as a provisional preview line instead of waiting for
    the Japanese translation. Only computed when both sides are present;
    a `None` on either side (e.g. a schema-gap experiment predating
    `wall_clock_lag`, or an utterance whose translation never completed
    within the recorded window) drops that utterance from the joined set
    rather than guessing.

Also reports English-line flicker for the would-be preview line: word-level
normalized erasure (NE) of the `asr_interim` group, reusing
`flicker_metrics.group_asr_interim`/`normalized_erasure` directly rather than
recomputing the same thing a second way.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import (
    group_asr_interim,
    group_translation_by_utterance,
)
from real_time_translation.experiments.stats import mean, median


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile (pct in [0, 100]); None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _first_wall_clock_lag(events: list[dict], kinds: tuple[str, ...]) -> float | None:
    for e in events:
        if e.get("kind") in kinds and e.get("wall_clock_lag") is not None:
            return e.get("wall_clock_lag")
    return None


@dataclass(frozen=True)
class UtterancePreviewTiming:
    asr_start_time: float
    time_to_first_interim_s: float | None
    time_to_first_translation_s: float | None
    preview_gain_s: float | None
    asr_ne_word: float | None


def utterance_preview_timings(events: list[dict]) -> list[UtterancePreviewTiming]:
    asr_groups = {g.key[0]: g for g in group_asr_interim(events)}
    translation_spans = {s.key[0]: s for s in group_translation_by_utterance(events)}

    # Per-utterance event slices, so each side's "first" lookup only sees its
    # own utterance's events (same grouping key, asr_start_time) rather than
    # the whole experiment's event stream.
    events_by_start: dict[float, list[dict]] = {}
    for e in events:
        if e.get("kind") not in (
            "asr_interim",
            "translation_partial",
            "translation_complete",
        ):
            continue
        start = e.get("asr_start_time", 0.0)
        events_by_start.setdefault(start, []).append(e)

    results: list[UtterancePreviewTiming] = []
    for start, asr_group in asr_groups.items():
        own_events = events_by_start.get(start, [])
        first_interim_lag = _first_wall_clock_lag(own_events, ("asr_interim",))
        span = translation_spans.get(start)
        first_translation_lag = (
            _first_wall_clock_lag(
                own_events, ("translation_partial", "translation_complete")
            )
            if span is not None
            else None
        )
        gain = (
            first_translation_lag - first_interim_lag
            if first_interim_lag is not None and first_translation_lag is not None
            else None
        )
        results.append(
            UtterancePreviewTiming(
                asr_start_time=start,
                time_to_first_interim_s=first_interim_lag,
                time_to_first_translation_s=first_translation_lag,
                preview_gain_s=gain,
                asr_ne_word=asr_group.ne,
            )
        )
    return results


@dataclass(frozen=True)
class PreviewTimingReport:
    json_path: str
    experiment_name: str
    num_utterances: int
    num_joined: int
    time_to_first_interim_mean_s: float | None
    time_to_first_interim_median_s: float | None
    time_to_first_interim_p90_s: float | None
    time_to_first_translation_mean_s: float | None
    time_to_first_translation_median_s: float | None
    time_to_first_translation_p90_s: float | None
    preview_gain_mean_s: float | None
    preview_gain_median_s: float | None
    asr_ne_word_mean: float | None


def analyze_experiment(path: Path) -> PreviewTimingReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    timings = utterance_preview_timings(events)
    interims = [
        t.time_to_first_interim_s
        for t in timings
        if t.time_to_first_interim_s is not None
    ]
    translations = [
        t.time_to_first_translation_s
        for t in timings
        if t.time_to_first_translation_s is not None
    ]
    gains = [t.preview_gain_s for t in timings if t.preview_gain_s is not None]
    ne_scores = [t.asr_ne_word for t in timings if t.asr_ne_word is not None]
    return PreviewTimingReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_utterances=len(timings),
        num_joined=len(gains),
        time_to_first_interim_mean_s=mean(interims),
        time_to_first_interim_median_s=median(interims),
        time_to_first_interim_p90_s=_percentile(interims, 90),
        time_to_first_translation_mean_s=mean(translations),
        time_to_first_translation_median_s=median(translations),
        time_to_first_translation_p90_s=_percentile(translations, 90),
        preview_gain_mean_s=mean(gains),
        preview_gain_median_s=median(gains),
        asr_ne_word_mean=mean(ne_scores),
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
        default=Path("experiments/interim_preview_timing.csv"),
        help="Sidecar CSV to write (kept separate from results.csv, same "
        "precedent as flicker_metrics.py).",
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[PreviewTimingReport] = []
    all_interims: list[float] = []
    all_translations: list[float] = []
    all_gains: list[float] = []
    for path in paths:
        try:
            r = analyze_experiment(path)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if r.num_utterances == 0:
            continue
        reports.append(r)
        data = json.loads(path.read_text(encoding="utf-8"))
        for t in utterance_preview_timings(data.get("results", {}).get("events", [])):
            if t.time_to_first_interim_s is not None:
                all_interims.append(t.time_to_first_interim_s)
            if t.time_to_first_translation_s is not None:
                all_translations.append(t.time_to_first_translation_s)
            if t.preview_gain_s is not None:
                all_gains.append(t.preview_gain_s)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_utterances",
                "num_joined",
                "time_to_first_interim_mean_s",
                "time_to_first_interim_median_s",
                "time_to_first_interim_p90_s",
                "time_to_first_translation_mean_s",
                "time_to_first_translation_median_s",
                "time_to_first_translation_p90_s",
                "preview_gain_mean_s",
                "preview_gain_median_s",
                "asr_ne_word_mean",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_utterances,
                    r.num_joined,
                    r.time_to_first_interim_mean_s,
                    r.time_to_first_interim_median_s,
                    r.time_to_first_interim_p90_s,
                    r.time_to_first_translation_mean_s,
                    r.time_to_first_translation_median_s,
                    r.time_to_first_translation_p90_s,
                    r.preview_gain_mean_s,
                    r.preview_gain_median_s,
                    r.asr_ne_word_mean,
                ]
            )

    print(f"analyzed {len(reports)} experiment(s) with event logs -> {args.out_csv}")
    if all_interims:
        print(
            f"time_to_first_interim_s across {len(all_interims)} utterance(s): "
            f"mean={mean(all_interims):.3f} median={median(all_interims):.3f} "
            f"p90={_percentile(all_interims, 90):.3f}"
        )
    if all_translations:
        print(
            f"time_to_first_translation_s across {len(all_translations)} "
            f"utterance(s): mean={mean(all_translations):.3f} "
            f"median={median(all_translations):.3f} "
            f"p90={_percentile(all_translations, 90):.3f}"
        )
    if all_gains:
        print(
            f"preview_gain_s (translation - interim) across {len(all_gains)} "
            f"jointly-measured utterance(s): mean={mean(all_gains):.3f} "
            f"median={median(all_gains):.3f}"
        )


if __name__ == "__main__":
    main()

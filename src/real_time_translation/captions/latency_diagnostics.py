"""Quantify the scheduling-lag cost of `format_readable`'s max_duration cap.

`format_readable` guarantees every caption card its full computed reading
duration by holding-and-queueing (see `schedule_with_lag`), which means a
card can be shown later than the instant its translation was ready if an
earlier card is still using up its guaranteed reading time. `max_duration`
is the single biggest lever on that lag: it caps how long any one card
(especially a kanji-heavy 26-character card) is allowed to hold the queue.

This sweeps a list of candidate max_duration values against one or more
already-recorded experiment JSONs and reports, for each, the resulting
mean/p90/max scheduling lag alongside the readability-compliance CPS
violation rate at that setting -- the actual trade-off, not a guess.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from real_time_translation.captions.readability_metrics import analyze_readability
from real_time_translation.captions.subtitle_formatter import (
    _build_readable_items,
    extract_utterance_cues,
    schedule_adaptive,
    schedule_with_lag,
)


def _summarize(scheduled: list, label: float) -> dict[str, float]:
    cues = [cue for cue, _lag in scheduled]
    lags = [lag for _cue, lag in scheduled]
    report = analyze_readability(cues)
    return {
        "setting": label,
        "n_cards": len(cues),
        "mean_lag": statistics.mean(lags) if lags else 0.0,
        "p90_lag": statistics.quantiles(lags, n=10)[8] if len(lags) >= 2 else 0.0,
        "max_lag": max(lags) if lags else 0.0,
        "cps_violation_rate": report.cps_violation_rate,
    }


def sweep_max_duration(
    events: list[dict], max_durations: list[float]
) -> list[dict[str, float]]:
    raw = extract_utterance_cues(events)
    rows = []
    for max_duration in max_durations:
        items = _build_readable_items(
            raw,
            max_chars_per_line=13,
            max_lines=2,
            min_duration=5 / 6,
            max_duration=max_duration,
            reading_units_per_sec=4.0,
        )
        rows.append(_summarize(schedule_with_lag(items), max_duration))
    return rows


def sweep_catchup_window(
    events: list[dict], catchup_windows: list[float]
) -> list[dict[str, float]]:
    raw = extract_utterance_cues(events)
    items = _build_readable_items(
        raw,
        max_chars_per_line=13,
        max_lines=2,
        min_duration=5 / 6,
        max_duration=7.0,
        reading_units_per_sec=4.0,
    )
    rows = []
    for window in catchup_windows:
        scheduled = schedule_adaptive(items, min_duration=5 / 6, catchup_window=window)
        rows.append(_summarize(scheduled, window))
    return rows


def _print_table(title: str, header: str, rows: list[dict[str, float]]) -> None:
    print(f"\n=== {title} ===")
    print(header)
    for row in rows:
        print(
            f"{row['setting']:>8.1f}  {row['n_cards']:>7d}  "
            f"{row['mean_lag']:>8.2f}s  {row['p90_lag']:>7.2f}s  "
            f"{row['max_lag']:>7.2f}s  {row['cps_violation_rate'] * 100:>8.1f}%"
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, type=Path, nargs="+")
    parser.add_argument(
        "--max-duration", type=float, nargs="+", default=[7.0, 5.0, 3.0, 2.0]
    )
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="Sweep schedule_adaptive's catchup_window (max_duration fixed at 7.0) "
        "instead of the flat schedule_with_lag's max_duration.",
    )
    parser.add_argument(
        "--catchup-window", type=float, nargs="+", default=[10.0, 5.0, 3.0, 1.5]
    )
    args = parser.parse_args(argv)

    header = (
        f"{'setting':>8}  {'n_cards':>7}  {'mean_lag':>9}  "
        f"{'p90_lag':>8}  {'max_lag':>8}  {'cps_viol%':>9}"
    )
    for path in args.experiment:
        data = json.loads(path.read_text(encoding="utf-8"))
        events = data["results"]["events"]
        if args.adaptive:
            rows = sweep_catchup_window(events, args.catchup_window)
            _print_table(f"{path.name} (adaptive: catchup_window sweep)", header, rows)
        else:
            rows = sweep_max_duration(events, args.max_duration)
            _print_table(f"{path.name} (flat: max_duration sweep)", header, rows)


if __name__ == "__main__":
    main()

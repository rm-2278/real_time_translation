"""Test h-review-perceived-drops-short-cards retroactively against an
experiment JSON: are the human-perceived "翻訳飛ばし" (occasional skipped
translations) actually API failures, or do they come from (a) a segment's
translation never becoming its own visible on-screen cue at all, because a
later segment's text overwrote it before `render_honest_captions.build_cues`'s
own minimum hold time elapsed, or (b) a segment being shown, but for well
under a second, or (c) low ASR confidence producing a translation whose
source content was itself uncertain?

No new ASR/LLM calls -- this only replays `build_cues`'s own cue-cutting
algorithm (the function that rendered the captioned video the human
reviewed, see experiments/20260924_llm2024_8_part1_human_review.md) with
each event's `(asr_start_time, asr_end_time)` key tracked alongside its
text, instead of discarding the key the way `build_cues` itself does. This
is deliberately kept as a near-literal copy of `build_cues`'s cut logic
(same defaults) rather than calling it directly, specifically so "did this
segment's key ever become a cue, and for how long" can be answered per
segment -- `build_cues` only returns `Cue(start, end, text)` with no key,
so a segment whose translation text happens to collide with another
segment's text could not be told apart from the outside. Keep the two
loops in sync if `build_cues`'s algorithm changes.

Usage:
    python3 -m real_time_translation.experiments.perceived_drop_analysis \\
        experiments/20260918_llm2025_ep8_zenhan_full_lecture_v2_dict.json
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOW_CONFIDENCE_THRESHOLD = 0.6
SHORT_CUE_SECONDS = 1.0

# Must match render_honest_captions.build_cues's own defaults -- this is a
# key-tracking replay of that exact algorithm, not an independent model.
MIN_DISPLAY_SECONDS = 0.6
SECONDS_PER_CHAR = 0.055
UTTERANCE_END_HOLD_SECONDS = 1.2


@dataclass(frozen=True)
class SegmentOutcome:
    key: tuple[float, float]
    shown: bool
    cue_duration_s: float | None


def _hold_for(text: str, is_utterance_end: bool) -> float:
    hold = max(MIN_DISPLAY_SECONDS, len(text) * SECONDS_PER_CHAR)
    if is_utterance_end:
        hold += UTTERANCE_END_HOLD_SECONDS
    return hold


def replay_cue_outcomes(
    events: list[dict[str, Any]],
) -> dict[tuple[float, float], SegmentOutcome]:
    """Same cut logic as `render_honest_captions.build_cues`, but keeping
    each pending cue's originating `(asr_start_time, asr_end_time)` key so
    every segment's fate (shown, for how long, or silently overwritten
    before its own hold time elapsed) can be read back per segment."""
    translation_kinds = ("translation_partial", "translation_complete")
    translation_events = [
        e
        for e in events
        if e.get("kind") in translation_kinds and e.get("text", "").strip()
    ]
    outcomes: dict[tuple[float, float], SegmentOutcome] = {}
    if not translation_events:
        return outcomes

    def key_of(e: dict[str, Any]) -> tuple[float, float]:
        return (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))

    cue_start = translation_events[0]["playback_offset"]
    cue_text = translation_events[0]["text"].strip()
    cue_is_end = bool(translation_events[0].get("is_utterance_end", True))
    cue_key = key_of(translation_events[0])

    for event in translation_events[1:]:
        offset = event["playback_offset"]
        text = event["text"].strip()
        is_end = bool(event.get("is_utterance_end", True))
        key = key_of(event)

        if offset - cue_start < _hold_for(cue_text, cue_is_end):
            # cue_key's pending text is overwritten before its own hold
            # time elapsed -- it never became its own visible cue.
            outcomes.setdefault(cue_key, SegmentOutcome(cue_key, False, None))
            cue_text = text
            cue_is_end = is_end
            cue_key = key
            continue

        if text != cue_text:
            outcomes[cue_key] = SegmentOutcome(cue_key, True, offset - cue_start)
            cue_start = offset
        else:
            # identical text to what's already pending: the key changes
            # but nothing new becomes visible under this key independently.
            outcomes.setdefault(cue_key, SegmentOutcome(cue_key, False, None))
        cue_text = text
        cue_is_end = is_end
        cue_key = key

    final_hold = _hold_for(cue_text, cue_is_end)
    outcomes[cue_key] = SegmentOutcome(cue_key, True, final_hold)
    return outcomes


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    results = data["results"]
    segments = results["segments"]
    events = results["events"]
    outcomes = replay_cue_outcomes(events)

    n = len(segments)
    never_shown: list[int] = []
    short_shown: list[int] = []
    low_conf: list[int] = []
    for i, seg in enumerate(segments):
        text = (seg.get("translation") or "").strip()
        conf = seg.get("confidence", 1.0)
        if conf < LOW_CONFIDENCE_THRESHOLD:
            low_conf.append(i)
        if not text:
            continue
        key = (seg["asr_start_time"], seg["asr_end_time"])
        outcome = outcomes.get(key)
        if outcome is None or not outcome.shown:
            never_shown.append(i)
        elif (
            outcome.cue_duration_s is not None
            and outcome.cue_duration_s < SHORT_CUE_SECONDS
        ):
            short_shown.append(i)

    never_shown_set = set(never_shown)
    short_shown_set = set(short_shown)
    low_conf_set = set(low_conf)
    invisible_set = never_shown_set | short_shown_set  # "felt like a drop" candidates

    return {
        "experiment": data.get("experiment_name"),
        "n_segments": n,
        "never_shown_as_own_cue": {
            "count": len(never_shown_set),
            "rate": round(len(never_shown_set) / n, 4) if n else 0.0,
            "sample_segment_indices": never_shown[:20],
        },
        "shown_but_under_1s": {
            "count": len(short_shown_set),
            "rate": round(len(short_shown_set) / n, 4) if n else 0.0,
        },
        "low_confidence_lt_0_6": {
            "count": len(low_conf_set),
            "rate": round(len(low_conf_set) / n, 4) if n else 0.0,
            "segment_indices": sorted(low_conf_set),
        },
        "low_confidence_that_is_also_invisible_or_short": {
            "count": len(low_conf_set & invisible_set),
            "fraction_of_low_confidence": (
                round(len(low_conf_set & invisible_set) / len(low_conf_set), 4)
                if low_conf_set
                else None
            ),
        },
        "low_confidence_that_was_shown_normally": sorted(low_conf_set - invisible_set),
        "invisible_or_short_total": {
            "count": len(invisible_set),
            "rate": round(len(invisible_set) / n, 4) if n else 0.0,
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/perceived_drop_analysis.csv"),
    )
    args = parser.parse_args(argv)

    patterns = args.experiments or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    summaries = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("results", {}).get("events"):
                continue
            summary = analyze(data)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        summary["path"] = str(path)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.out:
        args.out.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "n_segments",
                "never_shown_count",
                "never_shown_rate",
                "shown_under_1s_count",
                "low_confidence_count",
                "low_confidence_rate",
                "low_conf_also_invisible_or_short",
                "invisible_or_short_total_count",
                "invisible_or_short_total_rate",
            ]
        )
        for s in summaries:
            writer.writerow(
                [
                    s["experiment"],
                    s["path"],
                    s["n_segments"],
                    s["never_shown_as_own_cue"]["count"],
                    s["never_shown_as_own_cue"]["rate"],
                    s["shown_but_under_1s"]["count"],
                    s["low_confidence_lt_0_6"]["count"],
                    s["low_confidence_lt_0_6"]["rate"],
                    s["low_confidence_that_is_also_invisible_or_short"]["count"],
                    s["invisible_or_short_total"]["count"],
                    s["invisible_or_short_total"]["rate"],
                ]
            )
    print(f"analyzed {len(summaries)} experiment(s) -> {args.out_csv}")


if __name__ == "__main__":
    main()

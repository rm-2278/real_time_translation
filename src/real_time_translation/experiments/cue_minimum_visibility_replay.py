"""Test h-cue-minimum-visibility-guarantee retroactively against every
experiment JSON: if `render_honest_captions.build_cues`'s cut logic forced
a short cue (at the `min_display_seconds` floor only, no
`utterance_end_hold_seconds` extension) for a segment it would otherwise
silently overwrite with zero screen time, does that actually fix the
never-shown effect found by h-review-perceived-drops-short-cards (29.8%
pooled / 21.75% on the human-reviewed full lecture), or does it just
convert those segments into the milder-but-real "shown but under
SHORT_CUE_SECONDS" flicker bucket that currently sits at 0%?

No new ASR/LLM calls -- this is a near-literal copy of `build_cues`'s cut
algorithm (same defaults, same key-tracking approach as
`perceived_drop_analysis.py`), with one change: when a pending cue would be
overwritten before its own `_hold_for(...)` elapses AND the incoming text
differs from the pending text, a forced `min_display_seconds`-only cue is
emitted for the superseded text before the next cue's clock starts, instead
of discarding it with zero screen time. Keep the two loops in sync if
`build_cues`'s or `perceived_drop_analysis.py`'s algorithm changes.

Usage:
    python3 -m real_time_translation.experiments.cue_minimum_visibility_replay \\
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

SHORT_CUE_SECONDS = 1.0

# Must match render_honest_captions.build_cues's own defaults.
MIN_DISPLAY_SECONDS = 0.6
SECONDS_PER_CHAR = 0.055
UTTERANCE_END_HOLD_SECONDS = 1.2


@dataclass(frozen=True)
class SegmentOutcome:
    key: tuple[float, float]
    shown: bool
    cue_duration_s: float | None
    forced: bool


def _hold_for(text: str, is_utterance_end: bool) -> float:
    hold = max(MIN_DISPLAY_SECONDS, len(text) * SECONDS_PER_CHAR)
    if is_utterance_end:
        hold += UTTERANCE_END_HOLD_SECONDS
    return hold


def replay_cue_outcomes_with_min_visibility(
    events: list[dict[str, Any]],
) -> tuple[dict[tuple[float, float], SegmentOutcome], float]:
    """Same cut logic as `build_cues`, except a pending cue that would be
    silently overwritten before its own hold time elapses is instead given
    a forced `MIN_DISPLAY_SECONDS`-only cue (no utterance-end extension),
    and the next cue's clock starts from the end of that forced cue rather
    than from the overwriting event's real arrival offset.

    Returns (per-segment outcomes, cumulative end-of-clip drift in seconds
    this introduces relative to the real event timeline -- i.e. how much
    later the final cue's virtual start is than the final event's real
    `playback_offset`).
    """
    translation_kinds = ("translation_partial", "translation_complete")
    translation_events = [
        e
        for e in events
        if e.get("kind") in translation_kinds and e.get("text", "").strip()
    ]
    outcomes: dict[tuple[float, float], SegmentOutcome] = {}
    if not translation_events:
        return outcomes, 0.0

    def key_of(e: dict[str, Any]) -> tuple[float, float]:
        return (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))

    cue_start = translation_events[0]["playback_offset"]
    cue_text = translation_events[0]["text"].strip()
    cue_is_end = bool(translation_events[0].get("is_utterance_end", True))
    cue_key = key_of(translation_events[0])
    last_real_offset = cue_start

    for event in translation_events[1:]:
        offset = event["playback_offset"]
        text = event["text"].strip()
        is_end = bool(event.get("is_utterance_end", True))
        key = key_of(event)
        last_real_offset = max(last_real_offset, offset)

        if offset - cue_start < _hold_for(cue_text, cue_is_end):
            if text == cue_text:
                # Identical text re-sent: nothing new to show, same as the
                # unmodified algorithm -- no forced cue needed.
                outcomes.setdefault(
                    cue_key, SegmentOutcome(cue_key, False, None, False)
                )
                cue_text = text
                cue_is_end = is_end
                cue_key = key
                continue
            # Would otherwise be silently overwritten with zero screen
            # time. Force a MIN_DISPLAY_SECONDS-only cue for it instead,
            # and push the next cue's clock out to the end of this forced
            # cue (not to `offset`) so it still gets its floor duration.
            forced_duration = MIN_DISPLAY_SECONDS
            outcomes[cue_key] = SegmentOutcome(cue_key, True, forced_duration, True)
            cue_start = cue_start + forced_duration
            cue_text = text
            cue_is_end = is_end
            cue_key = key
            continue

        if text != cue_text:
            outcomes[cue_key] = SegmentOutcome(cue_key, True, offset - cue_start, False)
            cue_start = offset
        else:
            outcomes.setdefault(cue_key, SegmentOutcome(cue_key, False, None, False))
        cue_text = text
        cue_is_end = is_end
        cue_key = key

    final_hold = _hold_for(cue_text, cue_is_end)
    outcomes[cue_key] = SegmentOutcome(cue_key, True, final_hold, False)
    # How much later this cue's virtual start sits than the last real event's
    # arrival, purely from accumulated forced-cue pushes earlier in the clip.
    end_of_clip_drift = max(0.0, cue_start - last_real_offset)
    return outcomes, end_of_clip_drift


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    results = data["results"]
    segments = results["segments"]
    events = results["events"]
    outcomes, drift_s = replay_cue_outcomes_with_min_visibility(events)

    n = len(segments)
    never_shown: list[int] = []
    shown_under_1s: list[int] = []
    forced_count = 0
    for i, seg in enumerate(segments):
        text = (seg.get("translation") or "").strip()
        if not text:
            continue
        key = (seg["asr_start_time"], seg["asr_end_time"])
        outcome = outcomes.get(key)
        if outcome is None or not outcome.shown:
            never_shown.append(i)
            continue
        if outcome.forced:
            forced_count += 1
        is_short = (
            outcome.cue_duration_s is not None
            and outcome.cue_duration_s < SHORT_CUE_SECONDS
        )
        if is_short:
            shown_under_1s.append(i)

    never_shown_set = set(never_shown)
    shown_under_1s_set = set(shown_under_1s)

    return {
        "experiment": data.get("experiment_name"),
        "n_segments": n,
        "never_shown_as_own_cue": {
            "count": len(never_shown_set),
            "rate": round(len(never_shown_set) / n, 4) if n else 0.0,
        },
        "shown_but_under_1s": {
            "count": len(shown_under_1s_set),
            "rate": round(len(shown_under_1s_set) / n, 4) if n else 0.0,
        },
        "forced_min_visibility_cues": {
            "count": forced_count,
            "rate": round(forced_count / n, 4) if n else 0.0,
        },
        "end_of_clip_drift_seconds": round(drift_s, 3),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="*", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/cue_minimum_visibility_replay.csv"),
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
                "shown_under_1s_rate",
                "forced_min_visibility_count",
                "forced_min_visibility_rate",
                "end_of_clip_drift_seconds",
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
                    s["shown_but_under_1s"]["rate"],
                    s["forced_min_visibility_cues"]["count"],
                    s["forced_min_visibility_cues"]["rate"],
                    s["end_of_clip_drift_seconds"],
                ]
            )
    print(f"analyzed {len(summaries)} experiment(s) -> {args.out_csv}")


if __name__ == "__main__":
    main()

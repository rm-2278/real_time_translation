"""h-soft-anchor-truncation-detector: a cheap, judge()-free length/overlap
sanity check for intermittent catastrophic truncation in soft_anchor_replay.

Follows up directly on h-soft-anchor-repeat-variance-decomposition's cycle 70
discovery: on the guest-talk clip's span 73, soft_anchor_replay's repeat 0
produced a correct full translation but repeat 1 silently dropped an entire
preceding sentence, outputting only the tail clause. This went completely
undetected by translation_fidelity_judge.judge() because judge() is only
ever called on repeats[0] (ANALYZE_RESULTS's own established cycle-19
caveat), and repeats[0] for span 73 happened to be the clean repeat.

This script implements recommendation (a) from that result_summary: for
every span+condition group with >=2 repeats, compare each repeat's final
cumulative-translation character length against its longest sibling repeat
in the same span+condition group, and flag any repeat whose length falls
below FLAG_RATIO of that sibling's length. Span 73 is used as the one known
real calibration point (its actual ratio is reported in the output so the
chosen threshold is auditable, not picked arbitrarily).

Pure Python string-length arithmetic over data already on disk -- no live
Deepgram/Gemini/OpenAI call of any kind, no new translate_stream or judge()
calls, no results.csv row (same replay/analysis-script precedent as
flicker_metrics.py / prefix_lock_replay.py /
soft_anchor_repeat_variance_decomposition.py).

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_truncation_detector
"""

from __future__ import annotations

import json
from pathlib import Path

SOURCES = {
    "original_clip": Path("experiments/20260919_h_hard_prefix_lock_replay.json"),
    "guest_talk_clip": Path(
        "experiments/20260919_h_soft_anchor_generalization_guest_talk.json"
    ),
}
# Calibrated against span 73's real soft_anchor_replay instance (repeat 1 is
# ~22% of repeat 0's length). 40% gives ~1.8x margin above that known case
# while still well below a plausible "genuinely shorter but complete"
# rephrasing, which this repo's existing replay data shows rarely drops
# below ~60-70% of a sibling's length for ordinary wording variation.
FLAG_RATIO = 0.4
OUT_JSON = Path("experiments/20261022_h_soft_anchor_truncation_detector.json")


def _final_text(repeat: list[str]) -> str:
    return repeat[-1] if repeat else ""


def _analyze_clip(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    spans = data["results"]["spans"]
    conditions = sorted(
        {cond for span in spans for cond in span.get("condition_repeats", {})}
    )
    per_condition: dict[str, dict] = {}
    for condition in conditions:
        groups_checked = 0
        flagged: list[dict] = []
        for span in spans:
            repeats = span.get("condition_repeats", {}).get(condition)
            if not repeats or len(repeats) < 2:
                continue
            lengths = [len(_final_text(r)) for r in repeats]
            if max(lengths) == 0:
                continue
            groups_checked += 1
            longest = max(lengths)
            for idx, length in enumerate(lengths):
                if length < FLAG_RATIO * longest:
                    flagged.append(
                        {
                            "span_index": span["span_index"],
                            "repeat_index": idx,
                            "repeat_length": length,
                            "longest_sibling_length": longest,
                            "ratio": round(length / longest, 4),
                            "source_full_length": len(span.get("source_full", "")),
                            "flagged_text": _final_text(repeats[idx]),
                        }
                    )
        per_condition[condition] = {
            "groups_checked": groups_checked,
            "num_flagged": len(flagged),
            "flagged": flagged,
        }
    return per_condition


def main() -> None:
    results: dict[str, dict] = {}
    for clip_name, path in SOURCES.items():
        results[clip_name] = _analyze_clip(path)

    # Sanity check: span 73 (guest_talk_clip, soft_anchor_replay) must be
    # recovered, per the predicted_effect's explicit calibration requirement.
    guest_talk_soft_anchor = results.get("guest_talk_clip", {}).get(
        "soft_anchor_replay", {}
    )
    span73_flags = [
        f
        for f in guest_talk_soft_anchor.get("flagged", [])
        if f["span_index"] == 73
    ]
    sanity_check_passed = len(span73_flags) == 1

    output = {
        "hypothesis_id": "h-soft-anchor-truncation-detector",
        "flag_ratio_threshold": FLAG_RATIO,
        "sanity_check_span73_recovered": sanity_check_passed,
        "span73_detail": span73_flags[0] if span73_flags else None,
        "results": results,
    }
    OUT_JSON.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"Sanity check (span 73 recovered): {sanity_check_passed}")
    if span73_flags:
        print(f"  span 73 ratio: {span73_flags[0]['ratio']}")
    for clip_name, per_condition in results.items():
        print(f"\n{clip_name}:")
        for condition, stats in per_condition.items():
            print(
                f"  {condition}: {stats['num_flagged']}/{stats['groups_checked']} "
                f"groups flagged"
            )
            for f in stats["flagged"]:
                print(
                    f"    span {f['span_index']} repeat {f['repeat_index']}: "
                    f"{f['repeat_length']} vs sibling "
                    f"{f['longest_sibling_length']} (ratio {f['ratio']}, "
                    f"source_full_length {f['source_full_length']})"
                )
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()

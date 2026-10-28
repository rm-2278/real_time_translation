"""h-gate-utterance-end-temporal-blindness: does the soft-anchor disfluency
gate fire almost entirely on utterance-final batches, where by construction
no future audio can arrive to justify holding back -- the same
"temporally-blind over-reading" pathology reina-santan2026-readwrite-overread-bias
identifies in trained SimulST read/write policies?

soft_anchor_disfluency_gate_replay.py's gate condition is:
    gated = i > 0 and len(batch.delta_text.split()) < GATE_MIN_WORDS
with no `is_utterance_end` check at all (verified directly against that
file before writing this hypothesis). This script formalizes a one-off
count done during GENERATE_HYPOTHESES (which found 22/22 and 46/47 gated
batches were is_utterance_end=true across the two existing gated-condition
experiment JSONs) and extends it with:

1. An exact is_utterance_end fraction over ALL gated_batch_indices in both
   JSONs, with full counts (not just the percentage).
2. A per-transition erasure decomposition (reusing
   soft_anchor_gate_erasure_decomposition.py's exact method: LCP-based
   erasure between consecutive cumulative texts in condition_repeats,
   labeled gated_transition vs nongated_transition_in_gated_span), now
   further split by whether the gated transition's batch is
   is_utterance_end or not, and across BOTH source JSONs/BOTH source clips
   (the existing erasure-decomposition script only covered the guest-talk
   clip in the min3 JSON).
3. A whole-span judge_score_stats comparison (gated_soft_anchor vs
   soft_anchor_replay) restricted to gated spans, split the same way, using
   the per-span-per-condition noise-floor method from
   judge_noise_power_analysis.py's `_decompose_zero_effect_variance`
   (average per-group variance, not a pooled concatenation across spans)
   before drawing any conclusion about a quality difference.

Zero new Deepgram/LLM API calls -- pure retroactive analysis over the two
already-recorded experiment JSONs.

Usage:
  python3 -m real_time_translation.experiments.gate_utterance_end_temporal\
_blindness_check
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import (
    _longest_common_prefix_len,
)
from real_time_translation.experiments.stats import mean

SOURCE_JSONS = (
    Path("experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"),
    Path("experiments/20260922_h_soft_anchor_gate_min3_replay.json"),
)
OUT_JSON = Path("experiments/gate_utterance_end_temporal_blindness_check.json")
CONDITIONS = ("gated_soft_anchor", "soft_anchor_replay")


@dataclass(frozen=True)
class Transition:
    source_json: str
    source_clip: str
    condition: str
    span_index: int
    repeat: int
    transition_index: int  # t, the transition INTO batch t+1
    label: str  # "gated_transition" | "nongated_transition_in_gated_span"
    is_utterance_end: bool | None  # only meaningful for gated_transition
    erasure_normalized: float


def _per_transition_erasure(outs: list[str]) -> list[tuple[int, int]]:
    """[(erasure_chars, final_text_len)] for t=0..len(outs)-2, normalized
    against the REPEAT's own final (last-batch) text length -- identical
    method to soft_anchor_gate_erasure_decomposition.py's helper of the
    same name."""
    if len(outs) < 2:
        return []
    final_len = len(outs[-1])
    result = []
    for t in range(len(outs) - 1):
        lcp = _longest_common_prefix_len(outs[t], outs[t + 1])
        erasure = len(outs[t]) - lcp
        result.append((erasure, final_len))
    return result


def _label(t: int, gated_batch_indices: list[int]) -> str:
    return (
        "gated_transition"
        if (t + 1) in gated_batch_indices
        else "nongated_transition_in_gated_span"
    )


def _gated_batch_is_utterance_end(
    t: int, gated_batch_indices: list[int], batches: list[dict]
) -> bool | None:
    """For the gated_transition at t (i.e. (t+1) in gated_batch_indices),
    whether the gated batch itself (index t+1) is is_utterance_end. None if
    this transition isn't a gated_transition at all."""
    idx = t + 1
    if idx not in gated_batch_indices:
        return None
    if idx >= len(batches):
        return None
    return bool(batches[idx].get("is_utterance_end"))


def _decompose_zero_effect_variance(groups: list[list[float]]) -> dict:
    """judge_noise_power_analysis.py's `_decompose_zero_effect_variance`
    method, generalized to take pre-selected repeat-score groups directly
    (one list of repeat scores per span x condition group) rather than
    re-deriving them from a fixed source JSON's own span list -- same
    between/within-group decomposition, same rule (average per-group
    variance, don't pool raw scores first)."""
    group_means: list[float] = []
    within_vars: list[float] = []
    for scores in groups:
        if len(scores) < 2:
            continue
        m = sum(scores) / len(scores)
        group_means.append(m)
        within_vars.append(sum((s - m) ** 2 for s in scores) / (len(scores) - 1))
    if len(group_means) < 2 or not within_vars:
        return {
            "num_groups": len(group_means),
            "between_group_variance": None,
            "within_group_noise_stdev": None,
        }
    grand_mean = sum(group_means) / len(group_means)
    between_group_variance = sum((m - grand_mean) ** 2 for m in group_means) / len(
        group_means
    )
    within_group_mean_variance = sum(within_vars) / len(within_vars)
    return {
        "num_groups": len(group_means),
        "between_group_variance": between_group_variance,
        "between_group_stdev": between_group_variance**0.5,
        "within_group_noise_variance": within_group_mean_variance,
        "within_group_noise_stdev": within_group_mean_variance**0.5,
    }


def analyze() -> dict:
    is_utterance_end_count = 0
    is_utterance_end_total = 0
    per_file_counts: dict[str, dict] = {}

    tagged: list[Transition] = []
    # gated spans, keyed by (source_json, source_clip, span_index) ->
    # whether ALL of that span's gated batches are is_utterance_end (used
    # for the whole-span judge_score_stats comparison, since judge scores
    # are recorded per span, not per transition).
    gated_span_all_end: dict[tuple[str, str, int], bool] = {}
    gated_span_judge_scores: dict[tuple[str, str, int], dict[str, list[float]]] = {}

    for source_json in SOURCE_JSONS:
        data = json.loads(source_json.read_text(encoding="utf-8"))
        by_source = data["results"]["by_source"]
        file_end_count = 0
        file_total = 0
        for clip_name, val in by_source.items():
            for span in val.get("spans", []):
                gated_indices = span.get("gated_batch_indices") or []
                if not gated_indices:
                    continue
                batches = span.get("batches", [])
                span_key = (str(source_json), clip_name, span["span_index"])

                end_flags = []
                for idx in gated_indices:
                    is_end = (
                        bool(batches[idx].get("is_utterance_end"))
                        if idx < len(batches)
                        else None
                    )
                    if is_end is not None:
                        is_utterance_end_total += 1
                        file_total += 1
                        if is_end:
                            is_utterance_end_count += 1
                            file_end_count += 1
                    end_flags.append(is_end)
                known_flags = [f for f in end_flags if f is not None]
                gated_span_all_end[span_key] = all(known_flags)

                judge_stats = span.get("judge_score_stats", {})
                gated_span_judge_scores[span_key] = {
                    cond: judge_stats.get(cond, {}).get("scores", [])
                    for cond in CONDITIONS
                }

                for condition in CONDITIONS:
                    repeats = span.get("condition_repeats", {}).get(condition, [])
                    for repeat_idx, outs in enumerate(repeats):
                        if not outs:
                            continue
                        for t, (erasure, final_len) in enumerate(
                            _per_transition_erasure(outs)
                        ):
                            if final_len == 0:
                                continue
                            label = _label(t, gated_indices)
                            is_end = (
                                _gated_batch_is_utterance_end(t, gated_indices, batches)
                                if label == "gated_transition"
                                else None
                            )
                            tagged.append(
                                Transition(
                                    source_json=str(source_json),
                                    source_clip=clip_name,
                                    condition=condition,
                                    span_index=span["span_index"],
                                    repeat=repeat_idx,
                                    transition_index=t,
                                    label=label,
                                    is_utterance_end=is_end,
                                    erasure_normalized=erasure / final_len,
                                )
                            )

        per_file_counts[str(source_json)] = {
            "gated_batches_with_known_end_flag": file_total,
            "gated_batches_is_utterance_end": file_end_count,
            "fraction": (file_end_count / file_total) if file_total else None,
        }

    # Per-transition erasure, gated_transition only, split by is_utterance_end.
    erasure_by_condition_and_end_flag: dict[str, dict] = {}
    for condition in CONDITIONS:
        by_flag = {}
        for end_flag, flag_label in (
            (True, "is_utterance_end"),
            (False, "mid_utterance"),
        ):
            vals = [
                tr.erasure_normalized
                for tr in tagged
                if tr.condition == condition
                and tr.label == "gated_transition"
                and tr.is_utterance_end is end_flag
            ]
            by_flag[flag_label] = {
                "n": len(vals),
                "mean_erasure_normalized": mean(vals),
            }
        erasure_by_condition_and_end_flag[condition] = by_flag

    # Whole-span judge_score_stats comparison, restricted to gated spans,
    # split by whether ALL of that span's gated batches are
    # is_utterance_end (the "mid_utterance" bucket here means at least one
    # gated batch in the span was NOT is_utterance_end).
    judge_groups_by_flag: dict[str, dict[str, list[list[float]]]] = {
        "is_utterance_end": {c: [] for c in CONDITIONS},
        "mid_utterance_or_mixed": {c: [] for c in CONDITIONS},
    }
    for span_key, all_end in gated_span_all_end.items():
        flag_label = "is_utterance_end" if all_end else "mid_utterance_or_mixed"
        for condition in CONDITIONS:
            scores = gated_span_judge_scores[span_key][condition]
            if scores:
                judge_groups_by_flag[flag_label][condition].append(scores)

    judge_noise_decomposition_by_flag: dict[str, dict] = {}
    judge_mean_by_flag_and_condition: dict[str, dict] = {}
    for flag_label, by_condition in judge_groups_by_flag.items():
        judge_noise_decomposition_by_flag[flag_label] = {
            condition: _decompose_zero_effect_variance(groups)
            for condition, groups in by_condition.items()
        }
        judge_mean_by_flag_and_condition[flag_label] = {
            condition: mean([s for group in groups for s in group])
            for condition, groups in by_condition.items()
        }

    overall_fraction = (
        is_utterance_end_count / is_utterance_end_total
        if is_utterance_end_total
        else None
    )

    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_gate_utterance_end_temporal_blindness_check",
        "domain": None,
        "input": {
            "type": "retroactive_analysis",
            "source_jsons": [str(p) for p in SOURCE_JSONS],
            "note": (
                "Zero new Deepgram/LLM API calls -- reuses "
                "gated_batch_indices/batches[i].is_utterance_end/"
                "condition_repeats/judge_score_stats already recorded in "
                "both source JSONs. Erasure method identical to "
                "soft_anchor_gate_erasure_decomposition.py's "
                "_per_transition_erasure, generalized across both source "
                "JSONs and both source clips (the existing script only "
                "covered the guest-talk clip in the min3 JSON)."
            ),
        },
        "results": {
            "is_utterance_end_fraction_overall": overall_fraction,
            "is_utterance_end_count": is_utterance_end_count,
            "is_utterance_end_total_gated_batches": is_utterance_end_total,
            "per_source_json": per_file_counts,
            "erasure_gated_transition_by_condition_and_end_flag": (
                erasure_by_condition_and_end_flag
            ),
            "judge_score_mean_by_end_flag_and_condition": (
                judge_mean_by_flag_and_condition
            ),
            "judge_noise_decomposition_by_end_flag": judge_noise_decomposition_by_flag,
            "num_gated_spans_all_end": sum(
                1 for v in gated_span_all_end.values() if v
            ),
            "num_gated_spans_mid_or_mixed": sum(
                1 for v in gated_span_all_end.values() if not v
            ),
        },
        "conclusion": {
            "confirms_near_total_utterance_end_skew": (
                overall_fraction is not None and overall_fraction >= 0.95
            ),
        },
        "notes": (
            "h-gate-utterance-end-temporal-blindness. Formalizes the "
            "GENERATE_HYPOTHESES one-off count (22/22, 46/47) with exact "
            "per-file counts, adds a per-transition erasure split by "
            "is_utterance_end (reusing soft_anchor_gate_erasure_"
            "decomposition.py's method), and a whole-span judge_score_stats "
            "comparison restricted to gated spans, split by whether all "
            "gated batches in that span are is_utterance_end. The "
            "mid_utterance_or_mixed bucket is expected to be very small "
            "given the near-total skew found -- read its noise "
            "decomposition's num_groups before trusting any difference "
            "reported for it."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    out = analyze()
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {OUT_JSON}")
    print(json.dumps(out["results"], indent=2, ensure_ascii=False))
    print("\nconclusion:", json.dumps(out["conclusion"], indent=2))


if __name__ == "__main__":
    main()

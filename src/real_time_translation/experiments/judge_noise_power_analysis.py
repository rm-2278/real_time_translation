"""h-judge-noise-repeats-power-check: retroactive power analysis over the
per-repeat translation_fidelity_judge() noise floor that
h-soft-anchor-gate-recalibrated-noisefloor (cycle 20) was the first
hypothesis to actually measure directly (by calling judge() on every
repeat instead of just repeats[0]).

Across three hypotheses now (h-soft-anchor-disfluency-gate cycle 19,
h-soft-anchor-gate-recalibrated-noisefloor cycle 20, and
h-soft-anchor-gate-min-words-3 this cycle), small aggregate fidelity
differences (0.0-4.0 points) have been reported as "not confirmed"
because they sit inside a per-span judge() noise floor. This script asks,
purely retroactively (no new Deepgram/LLM calls): given the noise this
repo has actually measured, how many REPEATS would be needed to reliably
detect effects of that size, and what would that cost?

Data source: experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json
results.by_source[*].spans[*].judge_score_stats[condition] -- verified by
direct inspection (not assumed) to hold {"scores": [s0, s1], "mean":
float, "stdev": float, "range": [lo, hi]} per span per condition, with
exactly REPEATS=2 scores each in this file.

Two noise estimates are computed and compared:

1. "zero_effect" (reproduces cycle 20's own headroom-reported numbers):
   restricted to spans where a given condition-pair's gating had no
   effect (gated_batch_indices == [] for that span, so gated_soft_anchor
   should be code-path-identical to soft_anchor_replay) -- concatenates
   the per-repeat scores of both conditions on those spans and takes one
   pooled stdev.
2. "pooled_all" (broader, more data): every span x condition's own
   within-repeat stdev (valid same-code-path noise regardless of whether
   that specific span/condition pair happened to be a "zero effect" one,
   since two repeats of the *same* condition on the *same* span are
   always a same-code-path resample), pooled via sqrt(mean(variance)).

Power / sample-size model (explicit simplifying assumption, stated here
rather than silently assumed): this treats the per-span-per-condition
repeat noise (sigma, measured above) as the *only* source of variance in
a paired (same spans, two conditions) comparison -- i.e. it assumes the
*true* per-span effect is uniformly `delta` across all N spans, with zero
additional between-span heterogeneity in the true effect itself. This is
optimistic (a lower bound on the REPEATS actually needed): real between-
span heterogeneity, which this script does not attempt to measure, would
only increase the true requirement. For a paired one-sample z-test at
alpha=0.05 (two-sided) and power=0.80 detecting a mean per-span difference
of `delta`, using R repeats per condition per span and N spans:

    required R * N = 2 * (z_{alpha/2} + z_{beta})^2 * sigma^2 / delta^2

with z_{alpha/2}=1.959964 (alpha=0.05 two-sided), z_{beta}=0.841621
(power=0.80). Solving for R at the repo's actual N (span count per
source clip) gives the REPEATS this specific codebase would need for a
given target effect size, on each clip.

Cost model: reuses this session's own measured rate from cycle 20's run
(soft_anchor_gate_recalibrated_replay.py: $0.40 for 2046 total calls at
REPEATS=2, i.e. 1023 calls/REPEATS-unit across both source clips combined,
729 translate + 294 judge calls per REPEATS-unit) to convert a required R
into an estimated $ cost for a full two-clip, three-condition gate-replay
run at that REPEATS value: cost($) = (R / 2) * 0.40.

Usage:
  python3 -m real_time_translation.experiments.judge_noise_power_analysis
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

SOURCE_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
OUT_CSV = Path("experiments/judge_noise_power_analysis.csv")
OUT_JSON = Path("experiments/judge_noise_power_analysis_summary.json")

Z_ALPHA_2 = 1.959964  # alpha=0.05, two-sided
Z_BETA = 0.841621  # power=0.80

TARGET_EFFECT_SIZES = (2.0, 4.0, 10.0, 30.0)
REPEATS_GRID = (2, 3, 5, 10, 20, 50)

# Measured in cycle 20 (soft_anchor_gate_recalibrated_replay.py): $0.40 for
# 2046 total calls (1458 translate + 588 judge) at REPEATS=2, across both
# source clips combined in a single run.
CYCLE20_COST_USD_AT_REPEATS2 = 0.40


def _pooled_stdev_zero_effect(spans: list[dict]) -> tuple[float, int]:
    """Cycle 20's own definition: pool per-repeat scores across both
    conditions on spans where gating had no effect (gated_batch_indices
    empty), concatenate, take one stdev. Returns (stdev, num_scores)."""
    scores: list[float] = []
    for span in spans:
        if span.get("gated_batch_indices"):
            continue
        stats = span.get("judge_score_stats", {})
        for cond in ("soft_anchor_replay", "gated_soft_anchor"):
            scores.extend(stats.get(cond, {}).get("scores", []))
    if len(scores) < 2:
        return 0.0, len(scores)
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
    return math.sqrt(var), len(scores)


def _pooled_stdev_all(spans: list[dict]) -> tuple[float, int]:
    """Broader estimate: every span x condition's own within-repeat
    variance (each is a valid same-code-path 2-repeat resample regardless
    of gating effect), pooled as sqrt(mean(variance)) -- equal weight per
    span x condition since every one has exactly REPEATS=2 scores here."""
    variances: list[float] = []
    for span in spans:
        stats = span.get("judge_score_stats", {})
        for cond_stats in stats.values():
            scores = cond_stats.get("scores", [])
            if len(scores) < 2:
                continue
            mean = sum(scores) / len(scores)
            var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
            variances.append(var)
    if not variances:
        return 0.0, 0
    pooled_var = sum(variances) / len(variances)
    return math.sqrt(pooled_var), len(variances)


def _required_repeats(sigma: float, delta: float, num_spans: int) -> float:
    """required R*N = 2*(z_a/2+z_b)^2*sigma^2/delta^2; solve for R at
    fixed N=num_spans. Returns the raw (unrounded) R -- caller rounds up
    to the nearest value actually usable."""
    if delta <= 0 or num_spans <= 0:
        return math.inf
    required_r_times_n = 2 * (Z_ALPHA_2 + Z_BETA) ** 2 * sigma**2 / delta**2
    return required_r_times_n / num_spans


def _cost_for_repeats(total_repeats_across_both_clips: float) -> float:
    return (total_repeats_across_both_clips / 2) * CYCLE20_COST_USD_AT_REPEATS2


def analyze(source_json: Path) -> dict:
    data = json.loads(source_json.read_text(encoding="utf-8"))
    by_source = data["results"]["by_source"]

    per_source: dict[str, dict] = {}
    for name, v in by_source.items():
        spans = v["spans"]
        num_spans = v["num_usable_spans"]
        zero_effect_sigma, zero_effect_n = _pooled_stdev_zero_effect(spans)
        pooled_all_sigma, pooled_all_n = _pooled_stdev_all(spans)
        reported_sigma = v["noise_floor"]["stdev_across_same_code_path_resamples"]

        required_repeats_by_delta = {}
        for delta in TARGET_EFFECT_SIZES:
            r_zero = _required_repeats(zero_effect_sigma, delta, num_spans)
            r_pooled = _required_repeats(pooled_all_sigma, delta, num_spans)
            required_repeats_by_delta[str(delta)] = {
                "required_R_zero_effect_sigma": r_zero,
                "required_R_pooled_all_sigma": r_pooled,
            }

        per_source[name] = {
            "num_spans": num_spans,
            "zero_effect_sigma": zero_effect_sigma,
            "zero_effect_n_scores": zero_effect_n,
            "cycle20_reported_sigma": reported_sigma,
            "pooled_all_sigma": pooled_all_sigma,
            "pooled_all_n_span_conditions": pooled_all_n,
            "required_repeats_by_target_delta": required_repeats_by_delta,
        }

    # Combined two-clip run (both sources tested together in one
    # gate-replay run, per existing convention): use the higher-noise
    # (guest-talk) sigma as the binding constraint since that's what
    # determines the REPEATS needed for the whole run to have power on
    # both clips, and report cost against that R.
    worst_case_sigma_source = max(
        per_source.items(), key=lambda kv: kv[1]["pooled_all_sigma"]
    )[0]
    combined_rows = []
    for delta in TARGET_EFFECT_SIZES:
        r_needed_worst = max(
            per_source[name]["required_repeats_by_target_delta"][str(delta)][
                "required_R_pooled_all_sigma"
            ]
            for name in per_source
        )
        r_rounded = max(2, math.ceil(r_needed_worst))
        cost = _cost_for_repeats(r_rounded)
        combined_rows.append(
            {
                "target_delta": delta,
                "required_R_raw": r_needed_worst,
                "required_R_rounded": r_rounded,
                "estimated_cost_usd": round(cost, 2),
                "fits_per_batch_cap_3usd": cost <= 3.0,
            }
        )

    return {
        "per_source": per_source,
        "worst_case_sigma_source": worst_case_sigma_source,
        "combined_repeats_needed_by_target_delta": combined_rows,
        "assumptions": (
            "Paired one-sample z-test, alpha=0.05 two-sided, power=0.80. "
            "Treats measured within-span-repeat noise as the only variance "
            "source (assumes zero between-span heterogeneity in the true "
            "effect) -- this is an optimistic lower bound on REPEATS "
            "actually needed, not a full answer. Cost model scales "
            "linearly from cycle 20's measured $0.40 @ REPEATS=2 rate for "
            "the full two-clip, three-condition gate-replay structure."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = analyze(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "target_delta",
                "required_R_raw",
                "required_R_rounded",
                "estimated_cost_usd",
                "fits_per_batch_cap_3usd",
            ]
        )
        for row in result["combined_repeats_needed_by_target_delta"]:
            writer.writerow(
                [
                    row["target_delta"],
                    f"{row['required_R_raw']:.2f}",
                    row["required_R_rounded"],
                    row["estimated_cost_usd"],
                    row["fits_per_batch_cap_3usd"],
                ]
            )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

"""h-judge-holistic-vs-decomposed-repeat-noise: does asking
translation_fidelity_judge.judge() for the decomposed rubric fields
alongside the holistic score make that SAME holistic score noisier across
repeats, per unlp2026-holistic-vs-decomposed-scale's human-rater finding
that decomposed rating is LESS reliable than pure holistic rating?

Samples 8 (source_text, translation) pairs from experiments/20260916_
compression_actions_prompt.json's results['compression_actions']['segments']
-- segment index 5 (h-compression-actions-prompt-instruction's own
documented dropped-content case) plus 7 stride-3 segments (0, 3, 6, 9, 12,
15, 18), chosen for diversity away from the all-ceiling-effect failure mode
that made cycle 53's judge_schematic_adherence_replay.py replay
inconclusive. For each point, calls the EXISTING judge() (holistic-only)
and judge_decomposed() (decomposed-plus-holistic) K=3 times each, reusing
both functions unmodified -- neither prompt string is duplicated here.

Reports the within-point sample variance of each variant's 'score' field,
pooled across points as sqrt(mean of per-point variances) per PLAYBOOK.md's
ANALYZE_RESULTS convention (cycle 21 finding: do not pool raw scores across
points before computing a noise floor -- that inflates the estimate with
between-point quality differences).

Usage:
  python3 -m real_time_translation.experiments.judge_holistic_vs_decomposed_noise
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from real_time_translation.experiments.judge_schematic_adherence_replay import (
    judge_decomposed,
)
from real_time_translation.experiments.translation_fidelity_judge import judge

SOURCE_JSON = Path("experiments/20260916_compression_actions_prompt.json")
OUT_JSON = Path("experiments/judge_holistic_vs_decomposed_noise.json")
OUT_CSV = Path("experiments/judge_holistic_vs_decomposed_noise.csv")

SAMPLE_INDICES = (5, 0, 3, 6, 9, 12, 15, 18)
REPEATS = 3
# A per-point variance below this signals the same ceiling-effect failure
# mode that made cycle 53's replay inconclusive (near-identical repeat
# scores because the judge keeps returning the same value, not because
# there is genuinely no noise).
CEILING_NEAR_ZERO_VARIANCE = 1.0


def _sampled_points(data: dict) -> list[tuple[int, str, str]]:
    segments = {
        s["index"]: s for s in data["results"]["compression_actions"]["segments"]
    }
    return [
        (idx, segments[idx]["source_text"], segments[idx]["translation"])
        for idx in SAMPLE_INDICES
    ]


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    points = _sampled_points(data)

    rows: list[dict] = []
    holistic_variances: list[float] = []
    decomposed_variances: list[float] = []

    for idx, source_text, translation in points:
        holistic_scores = [
            judge(source_text, translation)["score"] for _ in range(REPEATS)
        ]
        decomposed_scores = [
            judge_decomposed(source_text, translation)["score"] for _ in range(REPEATS)
        ]

        holistic_var = statistics.variance(holistic_scores)
        decomposed_var = statistics.variance(decomposed_scores)
        holistic_variances.append(holistic_var)
        decomposed_variances.append(decomposed_var)

        rows.append(
            {
                "segment_index": idx,
                "holistic_only_scores": holistic_scores,
                "holistic_only_mean": statistics.mean(holistic_scores),
                "holistic_only_variance": holistic_var,
                "decomposed_embedded_holistic_scores": decomposed_scores,
                "decomposed_embedded_holistic_mean": statistics.mean(decomposed_scores),
                "decomposed_embedded_holistic_variance": decomposed_var,
            }
        )

    holistic_pooled_noise_stdev = statistics.mean(holistic_variances) ** 0.5
    decomposed_pooled_noise_stdev = statistics.mean(decomposed_variances) ** 0.5

    return {
        "sampled_n": len(points),
        "repeats_per_point": REPEATS,
        "sample_indices": list(SAMPLE_INDICES),
        "holistic_only_pooled_noise_stdev": holistic_pooled_noise_stdev,
        "decomposed_embedded_holistic_pooled_noise_stdev": (
            decomposed_pooled_noise_stdev
        ),
        "noise_ratio_decomposed_over_holistic": (
            decomposed_pooled_noise_stdev / holistic_pooled_noise_stdev
            if holistic_pooled_noise_stdev
            else None
        ),
        "ceiling_effect_flags": {
            "holistic_only_near_zero_variance_points": sum(
                1 for v in holistic_variances if v < CEILING_NEAR_ZERO_VARIANCE
            ),
            "decomposed_embedded_holistic_near_zero_variance_points": sum(
                1 for v in decomposed_variances if v < CEILING_NEAR_ZERO_VARIANCE
            ),
        },
        "rows": rows,
        "source": str(source_path),
        "notes": (
            "NEW gemini-3.1-flash-lite judge calls: 8 sampled (source_text, "
            "translation) pairs x 2 variants (judge() holistic-only, "
            "judge_decomposed() decomposed-plus-holistic) x 3 repeats = 48 "
            "calls total, zero new Deepgram spend, zero new translation "
            "generation (both texts reused verbatim from experiments/20260916_"
            "compression_actions_prompt.json's compression_actions.segments). "
            "Pooled noise is sqrt(mean of per-point variances), not a "
            "concatenated-then-pooled stdev across points (PLAYBOOK.md "
            "ANALYZE_RESULTS / cycle 21 convention). A near-zero per-point "
            "variance for most points in either variant signals the same "
            "ceiling-effect failure mode as cycle 53's replay and should be "
            "read as inconclusive for that variant, not as 'no noise'."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = parser.parse_args()

    result = analyze(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "segment_index",
                "holistic_only_mean",
                "holistic_only_variance",
                "decomposed_embedded_holistic_mean",
                "decomposed_embedded_holistic_variance",
            ]
        )
        for row in result["rows"]:
            writer.writerow(
                [
                    row["segment_index"],
                    row["holistic_only_mean"],
                    row["holistic_only_variance"],
                    row["decomposed_embedded_holistic_mean"],
                    row["decomposed_embedded_holistic_variance"],
                ]
            )

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

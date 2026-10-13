"""h-judge-verbosity-length-bias-retroactive: retroactive check of whether
translation_fidelity_judge.judge()'s 0-100 score correlates with output
length (verbosity), independent of the span being scored.

Motivated by this cycle's SEARCH_PAPERS/READ_PAPERS pass: arxiv2606-19544
("Reliability without Validity") measured verbosity bias as small (<0.011)
in their own cohort but explicitly said this repo's own rubric was not yet
checked; arxiv2509-20293 argues any aggregate LLM-judge score should be
checked for how much of the verdict a stated rubric actually explains
before being trusted.

Zero new API calls -- this is a pure retroactive analysis over two existing
experiment JSONs that pair per-repeat/per-condition judge scores with the
translation text that was actually judged:

  - experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json
    (judge_scores_per_repeat: a score PER repeat, so within the same
    span+condition the translation text differs incidentally across
    repeats while the code path/content intent is constant)
  - experiments/20260920_h_soft_anchor_disfluency_gate_replay.json
    (judge_scores: a single score per condition, judged on repeats[0] only,
    per cycle 19's finding about how that file was produced)

Two sub-checks, kept separate because they isolate different confounds:

  (1) WITHIN the same span+condition, across repeats (noisefloor-replay
      file only, the only one with >1 judge score per condition). Content/
      code path is ~constant here -- only incidental wording/length varies
      between repeats of the same translation call. If judge scores still
      track length here, that IS verbosity bias in the judge itself.
  (2) ACROSS conditions within the same span (both files). Conditions
      deliberately produce different-length output by design (e.g. a
      gating condition may genuinely drop content), so a length/score
      correlation here is *expected* and is NOT itself evidence of judge
      bias -- it is reported separately and never conflated with (1).
      Per-span mean-centering is applied to both variables before pooling
      across spans, mirroring cycle 21's between-span/within-span
      decomposition, since raw pooling would mix in each span's own
      baseline length/quality level.

Usage:
  python3 -m real_time_translation.experiments.judge_verbosity_bias_check
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

NOISEFLOOR_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
DISFLUENCY_JSON = Path("experiments/20260920_h_soft_anchor_disfluency_gate_replay.json")
OUT_JSON = Path("experiments/judge_verbosity_bias_check.json")
OUT_CSV = Path("experiments/judge_verbosity_bias_check.csv")

MIN_N_FOR_MEANINGFUL_CORRELATION = 20


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-indexed), ties get the mean rank -- standard
    Spearman tie-handling."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman_r(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    return _pearson_r(_ranks(xs), _ranks(ys))


def _correlation_report(xs: list[float], ys: list[float]) -> dict:
    n = len(xs)
    return {
        "n": n,
        "pearson_r": _pearson_r(xs, ys),
        "spearman_r": _spearman_r(xs, ys),
        "meaningful_sample_size": n >= MIN_N_FOR_MEANINGFUL_CORRELATION,
    }


def _last_cumulative_text(repeat: list[str]) -> str:
    return repeat[-1] if repeat else ""


def subcheck_within_span_condition_repeats(noisefloor_data: dict) -> dict:
    """(1) Within the same span+condition, across repeats
    (noisefloor-replay file only). Content is ~constant; only incidental
    length varies. Lengths and scores are NOT mean-centered here, since
    centering within a 2-repeat group would trivially force a correlation
    of the group's own pair of residuals -- the raw within-group pairs are
    pooled directly across all span x condition groups instead."""
    lengths: list[float] = []
    scores: list[float] = []
    by_source = noisefloor_data["results"]["by_source"]
    for source_val in by_source.values():
        for span in source_val["spans"]:
            condition_repeats = span["condition_repeats"]
            judge_scores_per_repeat = span["judge_scores_per_repeat"]
            for cond, repeats in condition_repeats.items():
                cond_scores = judge_scores_per_repeat.get(cond, [])
                for i, repeat in enumerate(repeats):
                    if i >= len(cond_scores):
                        continue
                    score = cond_scores[i].get("score")
                    if score is None:
                        # A failed judge() call (JSONDecodeError on the
                        # judge's own output) -- not a verbosity data
                        # point, exclude rather than crash.
                        continue
                    text = _last_cumulative_text(repeat)
                    lengths.append(float(len(text)))
                    scores.append(float(score))
    report = _correlation_report(lengths, scores)
    report["description"] = (
        "Within same span+condition, across repeats (noisefloor-replay file "
        "only) -- content ~constant, isolates verbosity bias in the judge "
        "itself from length differences that are purely incidental "
        "translation-call variance."
    )
    return report


PointTuple = tuple[str, int, str, float, float]


def _per_span_condition_points(
    data: dict, use_single_judge_scores: bool
) -> list[PointTuple]:
    """(2) Across conditions within the same span: one (length, score)
    point per span x condition, using repeats[0] for both files (the
    disfluency-gate file only ever has repeats[0] judged, so repeats[0] is
    used consistently for both rather than averaging the noisefloor file's
    extra repeat). Returns (source_name, span_index, condition, length,
    score) tuples."""
    points: list[PointTuple] = []
    by_source = data["results"]["by_source"]
    for source_name, source_val in by_source.items():
        for span in source_val["spans"]:
            condition_repeats = span["condition_repeats"]
            if use_single_judge_scores:
                scores_field = span["judge_scores"]
            else:
                scores_field = {
                    cond: (reps[0] if reps else None)
                    for cond, reps in span["judge_scores_per_repeat"].items()
                }
            for cond, repeats in condition_repeats.items():
                score_entry = scores_field.get(cond)
                if not repeats or score_entry is None:
                    continue
                score = score_entry.get("score")
                if score is None:
                    # A failed judge() call (JSONDecodeError on the
                    # judge's own output) -- exclude rather than crash.
                    continue
                text = _last_cumulative_text(repeats[0])
                length = float(len(text))
                point = (source_name, span["span_index"], cond, length, float(score))
                points.append(point)
    return points


def subcheck_across_conditions_same_span(
    noisefloor_data: dict, disfluency_data: dict
) -> dict:
    """(2) Across conditions within the same span, both files, after
    per-span mean-centering both length and score (per cycle 21's
    between-span/within-span decomposition pattern, applied here to avoid
    each span's own baseline length/quality level dominating the pooled
    correlation across many spans)."""
    all_points = _per_span_condition_points(
        noisefloor_data, use_single_judge_scores=False
    ) + _per_span_condition_points(disfluency_data, use_single_judge_scores=True)

    groups: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for source_name, span_index, _cond, length, score in all_points:
        groups.setdefault((source_name, span_index), []).append((length, score))

    centered_lengths: list[float] = []
    centered_scores: list[float] = []
    for pairs in groups.values():
        if len(pairs) < 2:
            continue
        mean_len = sum(p[0] for p in pairs) / len(pairs)
        mean_score = sum(p[1] for p in pairs) / len(pairs)
        for length, score in pairs:
            centered_lengths.append(length - mean_len)
            centered_scores.append(score - mean_score)

    report = _correlation_report(centered_lengths, centered_scores)
    report["num_span_groups"] = len(
        [pairs for pairs in groups.values() if len(pairs) >= 2]
    )
    report["description"] = (
        "Across conditions within the same span, both files, after "
        "per-span mean-centering length and score -- conditions deliberately "
        "differ in length by design, so a correlation here is expected and "
        "is NOT itself evidence of judge verbosity bias."
    )
    return report


def analyze(noisefloor_path: Path, disfluency_path: Path) -> dict:
    noisefloor_data = json.loads(noisefloor_path.read_text(encoding="utf-8"))
    disfluency_data = json.loads(disfluency_path.read_text(encoding="utf-8"))

    within = subcheck_within_span_condition_repeats(noisefloor_data)
    across = subcheck_across_conditions_same_span(noisefloor_data, disfluency_data)

    return {
        "subcheck_1_within_span_condition_across_repeats": within,
        "subcheck_2_across_conditions_same_span_mean_centered": across,
        "sources": {
            "noisefloor_replay": str(noisefloor_path),
            "disfluency_gate_replay": str(disfluency_path),
        },
        "notes": (
            "Zero new Deepgram/LLM API calls -- pure retroactive analysis over "
            "already-recorded condition_repeats/judge score fields. "
            "Sub-check 1 and sub-check 2 answer different questions and must "
            "not be conflated: only sub-check 1 isolates verbosity bias in the "
            "judge itself."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noisefloor", type=Path, default=NOISEFLOOR_JSON)
    parser.add_argument("--disfluency", type=Path, default=DISFLUENCY_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = parser.parse_args()

    result = analyze(args.noisefloor, args.disfluency)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["subcheck", "n", "pearson_r", "spearman_r", "meaningful_sample_size"]
        )
        for key in (
            "subcheck_1_within_span_condition_across_repeats",
            "subcheck_2_across_conditions_same_span_mean_centered",
        ):
            r = result[key]
            writer.writerow(
                [
                    key,
                    r["n"],
                    r["pearson_r"],
                    r["spearman_r"],
                    r["meaningful_sample_size"],
                ]
            )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

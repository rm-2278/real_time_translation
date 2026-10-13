"""h-judge-rubric-decomposition-schematic-adherence: does
translation_fidelity_judge.judge()'s single holistic 0-100 score hide rubric
collapse?

judge() currently asks for one holistic "information retention" score --
there is nothing to decompose in data already collected (verified directly
against translation_fidelity_judge.py before writing this hypothesis, see
papers.json _read_cross_reference_note_cycle53). This script instead issues
NEW Gemini calls with a decomposed-rubric prompt that asks for 4 named
sub-criteria scores (numbers_and_statistics,
named_entities_and_technical_terms, causal_logical_relationships,
overall_completeness) PLUS the existing holistic "score" field, all in ONE
response per sampled point -- same call-count cost as a plain re-judge, not
4x.

Two diagnostics, per arxiv2509-20293 ("When Judgment Becomes Noise"):
  (1) Pairwise Pearson r between the 4 sub-criteria across all sampled
      points. Correlations all >0.9 would mean the new rubric's criteria
      are not actually discriminating from each other (factor collapse),
      mirroring that paper's Arena-Hard Auto finding.
  (2) "Schematic adherence": a single pooled OLS of `score` regressed on
      the 4 sub-criteria across all sampled points, reported as one R^2 --
      how much of the judge's own holistic verdict its own stated rubric
      explains, within the SAME call (isolates this from cross-call judge
      noise, a distinct confound already established in cycle 19-21).

Sampled deterministically (span_index % 5 == 0, not random, for
reproducibility) from the 2 source files inside
experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json's
by_source, across all 3 conditions, repeats[0] only (same convention as
judge_verbosity_bias_check.py, to avoid conflating repeat noise with this
check).

Usage:
  python3 -m real_time_translation.experiments.judge_schematic_adherence_replay
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path

from real_time_translation.experiments.results_io import last_cumulative_text
from real_time_translation.experiments.stats import pearson_r

SOURCE_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
OUT_JSON = Path("experiments/judge_schematic_adherence_replay.json")
OUT_CSV = Path("experiments/judge_schematic_adherence_replay.csv")

SUB_CRITERIA = (
    "numbers_and_statistics",
    "named_entities_and_technical_terms",
    "causal_logical_relationships",
    "overall_completeness",
)
SPAN_SAMPLE_STRIDE = 5

_DECOMPOSED_JUDGE_PROMPT = """You are a strict bilingual (English/Japanese) \
fact-checker reviewing a real-time speech translation for information retention, not \
fluency. You will be shown the English source transcript (may contain ASR \
disfluencies -- ignore those) and its Japanese translation.

Score each of the following 0-100, independent of how natural the Japanese \
reads:
- numbers_and_statistics: fraction of numbers/quantities/statistics from \
the source that survive in the translation.
- named_entities_and_technical_terms: fraction of named entities and \
technical terms from the source that survive in the translation.
- causal_logical_relationships: fraction of causal/logical relationships \
between ideas in the source that survive in the translation.
- overall_completeness: fraction of the source's substantive content as a \
whole that survives in the translation.
- score: your own single overall 0-100 information-retention judgment for \
this pair, independent of the four scores above -- do not simply average \
them, make your own holistic call.

Respond with ONLY a JSON object, no markdown fences:
{{"numbers_and_statistics": <int 0-100>, \
"named_entities_and_technical_terms": <int 0-100>, \
"causal_logical_relationships": <int 0-100>, \
"overall_completeness": <int 0-100>, "score": <int 0-100>}}

<source_english>
{source}
</source_english>

<translation_japanese>
{translation}
</translation_japanese>
"""


def judge_decomposed(source: str, translation: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = _DECOMPOSED_JUDGE_PROMPT.format(source=source, translation=translation)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
    )
    text = response.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Judge did not return JSON: {text[:300]!r}")
    return json.loads(match.group(0))



def _sampled_points(data: dict) -> list[tuple[str, int, str, str, str]]:
    """Returns (source_name, span_index, condition, source_full, translation)
    for every sampled span x condition, repeats[0] only."""
    points: list[tuple[str, int, str, str, str]] = []
    by_source = data["results"]["by_source"]
    for source_name, source_val in by_source.items():
        for span in source_val["spans"]:
            if span["span_index"] % SPAN_SAMPLE_STRIDE != 0:
                continue
            source_full = span["source_full"]
            for cond, repeats in span["condition_repeats"].items():
                if not repeats:
                    continue
                translation = last_cumulative_text(repeats[0])
                if not translation:
                    continue
                points.append(
                    (source_name, span["span_index"], cond, source_full, translation)
                )
    return points



def _pairwise_correlations(rows: list[dict]) -> dict:
    out: dict[str, float | None] = {}
    for i, crit_a in enumerate(SUB_CRITERIA):
        for crit_b in SUB_CRITERIA[i + 1 :]:
            xs = [float(r[crit_a]) for r in rows]
            ys = [float(r[crit_b]) for r in rows]
            out[f"{crit_a}__vs__{crit_b}"] = pearson_r(xs, ys)
    return out


def _ols_r_squared(rows: list[dict]) -> float | None:
    """Pooled OLS of `score` on the 4 sub-criteria (plus intercept), one R^2
    across all sampled points -- schematic adherence at the rubric level,
    not per-point. Solved via the normal equations with plain Python lists
    (no numpy dependency), matching this repo's existing
    judge_verbosity_bias_check.py style of hand-rolled stats."""
    n = len(rows)
    if n <= len(SUB_CRITERIA) + 1:
        return None
    # Design matrix X (n x (k+1), intercept first), target vector y.
    X = [[1.0] + [float(r[c]) for c in SUB_CRITERIA] for r in rows]
    y = [float(r["score"]) for r in rows]
    k = len(SUB_CRITERIA) + 1

    # Normal equations: (X^T X) beta = X^T y, solved by Gaussian elimination.
    xtx = [
        [sum(X[row][i] * X[row][j] for row in range(n)) for j in range(k)]
        for i in range(k)
    ]
    xty = [sum(X[row][i] * y[row] for row in range(n)) for i in range(k)]

    # Augment and solve.
    aug = [xtx[i] + [xty[i]] for i in range(k)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-9:
            return None  # singular -- sub-criteria are perfectly collinear
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]
        for r in range(k):
            if r != col:
                factor = aug[r][col]
                aug[r] = [aug[r][c] - factor * aug[col][c] for c in range(k + 1)]
    beta = [aug[i][k] for i in range(k)]

    y_mean = sum(y) / n
    ss_tot = sum((v - y_mean) ** 2 for v in y)
    if ss_tot == 0:
        return None
    ss_res = 0.0
    for row in range(n):
        y_hat = sum(beta[i] * X[row][i] for i in range(k))
        ss_res += (y[row] - y_hat) ** 2
    return 1.0 - ss_res / ss_tot


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    points = _sampled_points(data)

    rows: list[dict] = []
    for source_name, span_index, cond, source_full, translation in points:
        result = judge_decomposed(source_full, translation)
        row = {
            "source_name": source_name,
            "span_index": span_index,
            "condition": cond,
            **{c: result.get(c) for c in SUB_CRITERIA},
            "score": result.get("score"),
        }
        rows.append(row)

    complete_rows = [
        r for r in rows if all(r.get(c) is not None for c in (*SUB_CRITERIA, "score"))
    ]

    return {
        "sampled_n": len(points),
        "complete_n": len(complete_rows),
        "span_sample_stride": SPAN_SAMPLE_STRIDE,
        "pairwise_subcriteria_correlations": _pairwise_correlations(complete_rows),
        "schematic_adherence_r_squared": _ols_r_squared(complete_rows),
        "rows": rows,
        "source": str(source_path),
        "notes": (
            "New Gemini calls (gemini-3.1-flash-lite), one per sampled point, each "
            "returning the 4 named sub-criteria plus the holistic 'score' field in "
            "one response -- zero new Deepgram spend, replay of already-recorded "
            "source_full / condition_repeats text from experiments/20260921_h_soft_"
            "anchor_gate_recalibrated_noisefloor_replay.json. Pairwise correlations "
            ">0.9 across most sub-criteria would mean the new decomposed rubric is "
            "itself collapsed/not discriminating (mirrors arxiv2509-20293's "
            "Arena-Hard Auto finding). schematic_adherence_r_squared is a SINGLE "
            "pooled OLS fit across all sampled points, not a per-point statistic -- "
            "it answers how well the judge's own stated sub-criteria explain its "
            "own holistic verdict, at the rubric level."
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
            ["source_name", "span_index", "condition", *SUB_CRITERIA, "score"]
        )
        for row in result["rows"]:
            writer.writerow(
                [row["source_name"], row["span_index"], row["condition"]]
                + [row.get(c) for c in SUB_CRITERIA]
                + [row.get("score")]
            )

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

"""h-judge-repeat-count-diminishing-returns: does translation_fidelity_judge.
judge()'s per-call noise shrink with K judge-repeats the way plain i.i.d.
scaling predicts, or does it converge faster/slower?

ruverbench2606 reports majority voting / repeat-averaging reduces LLM-judge
noise with diminishing returns as more repeat votes are added. Cycle 65
tested a different claim from the same paper (reasoning-before-score, see
h-judge-reasoning-before-score-noise / judge_reasoning_order_replay.py) and
flagged this repeat-count claim as untested. This repo's own judge-noise
work (cycles 19-22, cycle 65) has only ever used K=2 judge-repeats.

Reuses cycle 65's fixed-translation-text design exactly (same source JSON,
same 34 zero-effect span x condition groups sampled at span_index % 4 == 0,
same production score_first prompt only) but raises K from 2 to 5 per
group (170 total new judge calls), then, for each K' in 1..5, compares the
EMPIRICAL variance of the mean across all size-K' subsets of each group's 5
draws against the NAIVE theoretical prediction (each group's own 5-draw
sample variance divided by K'). A ratio near 1.0 at every K' supports plain
i.i.d. scaling; a ratio that grows with K signals the per-group draws are
positively correlated (repeats help less than naively assumed).

Does NOT modify translation_fidelity_judge.py or any production code --
replay-only. Zero new Deepgram spend, zero new translation generation.

Usage:
  python3 -m real_time_translation.experiments.judge_repeat_diminishing_returns
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
from pathlib import Path

from real_time_translation.experiments.results_io import last_cumulative_text
from real_time_translation.experiments.stats import mean, stdev

SOURCE_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
OUT_JSON = Path("experiments/judge_repeat_diminishing_returns.json")

SPAN_SAMPLE_STRIDE = 4
NOISE_CONDITIONS = ("soft_anchor_replay", "gated_soft_anchor")
K_MAX = 5

# Same production score_first prompt as judge_reasoning_order_replay.py /
# translation_fidelity_judge.py -- copied locally since the existing replay
# module exposes no importable, K-parameterized judge-call function.
_SCORE_FIRST_PROMPT = """You are a strict bilingual (English/Japanese) fact-checker \
reviewing a real-time speech translation for information retention, not \
fluency. You will be shown the English source transcript (may contain ASR \
disfluencies -- ignore those) and its Japanese translation.

Score 0-100: what fraction of the source's substantive content (facts, \
numbers, technical terms, named entities, causal/logical relationships) \
survives in the translation, independent of how natural the Japanese \
reads. A translation that is short but keeps every key point should score \
high; a fluent translation that drops a number or a technical term should \
score lower.

Respond with ONLY a JSON object, no markdown fences:
{{"score": <int 0-100>, "dropped_or_distorted": ["short phrase", ...], \
"notes": "one sentence"}}

<source_english>
{source}
</source_english>

<translation_japanese>
{translation}
</translation_japanese>
"""


def _judge_once(source: str, translation: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = _SCORE_FIRST_PROMPT.format(source=source, translation=translation)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
    )
    text = response.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Judge did not return JSON: {text[:300]!r}")
    return json.loads(match.group(0))


def _sampled_groups(data: dict) -> list[tuple[str, int, str, str, str]]:
    """Same sampling convention as judge_reasoning_order_replay.py: returns
    (source_name, span_index, condition, source_full, fixed_translation)
    for every sampled zero-effect span x condition group."""
    groups: list[tuple[str, int, str, str, str]] = []
    by_source = data["results"]["by_source"]
    for source_name, source_val in by_source.items():
        for span in source_val["spans"]:
            if span.get("gated_batch_indices"):
                continue
            if span["span_index"] % SPAN_SAMPLE_STRIDE != 0:
                continue
            source_full = span["source_full"]
            for cond in NOISE_CONDITIONS:
                repeats = span["condition_repeats"].get(cond) or []
                if not repeats:
                    continue
                translation = last_cumulative_text(repeats[0])
                if not translation:
                    continue
                groups.append(
                    (source_name, span["span_index"], cond, source_full, translation)
                )
    return groups


def _subset_mean_variance(draws: list[float], k: int) -> float | None:
    """Empirical variance of the mean across all size-k subsets (without
    replacement) of draws. None if fewer than 2 subsets exist (variance
    undefined) or k > len(draws)."""
    if k > len(draws):
        return None
    subset_means = [mean(list(combo)) for combo in itertools.combinations(draws, k)]
    return stdev(subset_means) ** 2 if stdev(subset_means) is not None else None


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    groups = _sampled_groups(data)

    per_group: list[dict] = []
    rows: list[dict] = []

    for source_name, span_index, cond, source_full, translation in groups:
        scores: list[float] = []
        for _ in range(K_MAX):
            result = _judge_once(source_full, translation)
            score = result.get("score")
            if score is None:
                continue
            scores.append(float(score))
            rows.append(
                {
                    "source_name": source_name,
                    "span_index": span_index,
                    "condition": cond,
                    "score": score,
                }
            )
        if len(scores) < K_MAX:
            continue
        single_draw_variance = stdev(scores) ** 2 if stdev(scores) is not None else None
        per_group.append(
            {
                "source_name": source_name,
                "span_index": span_index,
                "condition": cond,
                "scores": scores,
                "single_draw_variance": single_draw_variance,
            }
        )

    # For each K' in 1..K_MAX, average (across groups) the empirical
    # subset-mean variance and the naive theoretical prediction
    # (single_draw_variance / K').
    curve: dict[str, dict] = {}
    for k in range(1, K_MAX + 1):
        empirical_vars: list[float] = []
        theoretical_vars: list[float] = []
        for g in per_group:
            emp = _subset_mean_variance(g["scores"], k)
            if emp is not None:
                empirical_vars.append(emp)
            if g["single_draw_variance"] is not None:
                theoretical_vars.append(g["single_draw_variance"] / k)
        emp_mean = mean(empirical_vars)
        theo_mean = mean(theoretical_vars)
        has_valid_theo = emp_mean is not None and theo_mean not in (None, 0)
        ratio = emp_mean / theo_mean if has_valid_theo else None
        curve[str(k)] = {
            "n_groups_empirical": len(empirical_vars),
            "n_groups_theoretical": len(theoretical_vars),
            "mean_empirical_subset_mean_variance": emp_mean,
            "mean_theoretical_variance_over_k": theo_mean,
            "empirical_over_theoretical_ratio": ratio,
        }

    return {
        "sampled_groups": len(groups),
        "groups_with_full_k_max": len(per_group),
        "span_sample_stride": SPAN_SAMPLE_STRIDE,
        "noise_conditions": list(NOISE_CONDITIONS),
        "k_max": K_MAX,
        "diminishing_returns_curve": curve,
        "rows": rows,
        "source": str(source_path),
        "notes": (
            "For each K' in 1..K_MAX, 'mean_empirical_subset_mean_variance' is "
            "the actual variance of the mean across all C(5, K') same-size "
            "subsets of each group's 5 already-collected judge draws, averaged "
            "across groups. 'mean_theoretical_variance_over_k' is the naive "
            "i.i.d. prediction (each group's own 5-draw sample variance / K'), "
            "averaged the same way. A ratio near 1.0 at every K' supports plain "
            "i.i.d. scaling (ruverbench2606's diminishing returns is then just "
            "ordinary 1/K variance scaling, nothing repo-specific). A ratio "
            "that grows with K means repeats help less than naively assumed "
            "(positively correlated per-group judge draws); a ratio below 1.0 "
            "would mean repeats help MORE than naively assumed. This is a "
            "K_MAX=5-per-group check (34 groups) -- modest power, same spirit "
            "as cycles 19-22's own low-power caveats; report honestly."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = analyze(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

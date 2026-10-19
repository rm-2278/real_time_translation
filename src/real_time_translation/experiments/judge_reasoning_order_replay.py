"""h-judge-reasoning-before-score-noise: does asking translation_fidelity_
judge.judge() for a free-text reasoning field BEFORE the numeric score
reduce its per-call scoring noise?

rubriceval2603 (RubricEval) reports that explicit reasoning before the
verdict improves LLM-judge accuracy on rubric-item grading and reduces
inter-judge variance; ruverbench2606 independently confirms judge noise
under rubric-item verification is a general cross-domain phenomenon. No
judge prompt variant in this codebase has ever asked for a reasoning field
before a numeric score (verified against translation_fidelity_judge.py and
judge_schematic_adherence_replay.py before writing this hypothesis).

Separately, this script fixes a known conflation in cycle 19-21's existing
noise-floor baseline (experiments/judge_noise_power_analysis_summary.json):
that baseline called judge() once per REPEATS-translation-run, i.e. on a
freshly *generated* (and possibly different) translation each time, mixing
translation-generation stochasticity into the measured "judge noise". This
script instead fixes ONE translation text per sampled span x condition
(repeats[0]'s final cumulative text, same convention as
judge_schematic_adherence_replay.py) and calls judge() K=2 times on that
exact same fixed (source, translation) pair for each of two prompt
variants, isolating pure judge-scoring noise from translation noise.

Sampled deterministically (span_index % 4 == 0, zero-effect spans only --
gated_batch_indices == [] -- across both source clips'
soft_anchor_replay/gated_soft_anchor conditions, same stride-sampling
convention as judge_schematic_adherence_replay.py) to bound cost at ~34
span x condition groups, K=2 judge calls per variant per group = ~136
total new judge() calls. Zero new Deepgram spend, zero new translation
generation.

Usage:
  python3 -m real_time_translation.experiments.judge_reasoning_order_replay
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from real_time_translation.experiments.results_io import last_cumulative_text
from real_time_translation.experiments.stats import mean, stdev

SOURCE_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
OUT_JSON = Path("experiments/judge_reasoning_order_replay.json")

SPAN_SAMPLE_STRIDE = 4
NOISE_CONDITIONS = ("soft_anchor_replay", "gated_soft_anchor")
JUDGE_REPEATS = 2

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

_REASONING_FIRST_PROMPT = """You are a strict bilingual (English/Japanese) \
fact-checker reviewing a real-time speech translation for information retention, not \
fluency. You will be shown the English source transcript (may contain ASR \
disfluencies -- ignore those) and its Japanese translation.

First, reason step by step about what fraction of the source's substantive \
content (facts, numbers, technical terms, named entities, causal/logical \
relationships) survives in the translation, independent of how natural \
the Japanese reads. Only after that reasoning, give a 0-100 score. A \
translation that is short but keeps every key point should score high; a \
fluent translation that drops a number or a technical term should score \
lower.

Respond with ONLY a JSON object, no markdown fences, with "reasoning" as \
the FIRST key (write your reasoning before you decide the score) and \
"score" as the LAST key:
{{"reasoning": "2-3 sentences of step-by-step analysis", \
"dropped_or_distorted": ["short phrase", ...], "score": <int 0-100>}}

<source_english>
{source}
</source_english>

<translation_japanese>
{translation}
</translation_japanese>
"""

PROMPT_VARIANTS = {
    "score_first": _SCORE_FIRST_PROMPT,
    "reasoning_first": _REASONING_FIRST_PROMPT,
}


def _judge_once(prompt_template: str, source: str, translation: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = prompt_template.format(source=source, translation=translation)
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
    """Returns (source_name, span_index, condition, source_full,
    fixed_translation) for every sampled zero-effect span x condition
    group -- fixed_translation is repeats[0]'s final cumulative text,
    never regenerated."""
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


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    groups = _sampled_groups(data)

    per_variant_groups: dict[str, list[dict]] = {name: [] for name in PROMPT_VARIANTS}
    rows: list[dict] = []

    for source_name, span_index, cond, source_full, translation in groups:
        for variant_name, prompt_template in PROMPT_VARIANTS.items():
            scores: list[float] = []
            for _ in range(JUDGE_REPEATS):
                result = _judge_once(prompt_template, source_full, translation)
                score = result.get("score")
                if score is None:
                    continue
                scores.append(float(score))
                rows.append(
                    {
                        "source_name": source_name,
                        "span_index": span_index,
                        "condition": cond,
                        "variant": variant_name,
                        "score": score,
                    }
                )
            group_stdev = stdev(scores)
            group_mean = mean(scores)
            per_variant_groups[variant_name].append(
                {
                    "source_name": source_name,
                    "span_index": span_index,
                    "condition": cond,
                    "scores": scores,
                    "mean": group_mean,
                    "stdev": group_stdev,
                }
            )

    summary_by_variant: dict[str, dict] = {}
    for variant_name, g in per_variant_groups.items():
        within_group_stdevs = [grp["stdev"] for grp in g if grp["stdev"] is not None]
        within_group_variances = [s**2 for s in within_group_stdevs]
        mean_within_group_variance = mean(within_group_variances)
        summary_by_variant[variant_name] = {
            "num_groups": len(g),
            "num_groups_with_stdev": len(within_group_stdevs),
            "mean_within_group_variance": mean_within_group_variance,
            "within_group_noise_stdev": (
                mean_within_group_variance**0.5
                if mean_within_group_variance is not None
                else None
            ),
        }

    # Paired per-group comparison: for groups present in both variants,
    # reasoning_first_stdev - score_first_stdev (negative = reasoning-first
    # is less noisy for that group).
    paired_deltas: list[float] = []
    score_first_by_key = {
        (g["source_name"], g["span_index"], g["condition"]): g["stdev"]
        for g in per_variant_groups.get("score_first", [])
        if g["stdev"] is not None
    }
    for g in per_variant_groups.get("reasoning_first", []):
        key = (g["source_name"], g["span_index"], g["condition"])
        if g["stdev"] is None or key not in score_first_by_key:
            continue
        paired_deltas.append(g["stdev"] - score_first_by_key[key])

    return {
        "sampled_groups": len(groups),
        "span_sample_stride": SPAN_SAMPLE_STRIDE,
        "judge_repeats_per_variant": JUDGE_REPEATS,
        "noise_conditions": list(NOISE_CONDITIONS),
        "summary_by_variant": summary_by_variant,
        "paired_per_group_stdev_delta": {
            "n_paired_groups": len(paired_deltas),
            "mean_delta_reasoning_minus_score_first": mean(paired_deltas),
            "deltas": paired_deltas,
        },
        "rows": rows,
        "source": str(source_path),
        "notes": (
            "Fixed-text design: translation text is repeats[0]'s final cumulative "
            "text, held constant across both prompt variants and across the "
            f"{JUDGE_REPEATS} judge-repeats within each variant -- only the judge "
            "call varies, isolating judge-scoring noise from translation-generation "
            "stochasticity (a known conflation in cycle 19-21's existing noise-floor "
            "baseline, which called judge() once per separately-generated "
            "translation repeat). within_group_noise_stdev is sqrt(mean(per-group "
            "variance)), matching judge_noise_power_analysis.py's "
            "_decompose_zero_effect_variance() within-group convention. A negative "
            "mean_delta_reasoning_minus_score_first supports rubriceval2603's "
            "reasoning-before-score finding generalizing to this task; report "
            "honestly if the difference is small relative to either variant's own "
            "noise, since this is a single K=2 check, not a well-powered test."
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

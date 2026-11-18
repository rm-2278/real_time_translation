"""h-judge-cross-gemini-model-agreement-check: does
translation_fidelity_judge.judge()'s single-judge-model design
(hardcoded gemini-3.1-flash-lite) hide disagreement that a second,
distinct model would catch?

h-judge-cross-model-agreement-check (gpt-4o-mini vs. gemini-3.1-flash-lite)
has sat queued+approved since cycle 56 but is environment-blocked in every
sandbox checked so far: api.openai.com is CONNECT-rejected by the egress
proxy (see PLAYBOOK.md RUN_EXPERIMENTS), while
generativelanguage.googleapis.com has been reachable in every cycle. This
hypothesis substitutes a second, DISTINCT Gemini model (gemini-3.6-flash,
falling back to gemini-3.5-flash-lite) as the cheapest currently-runnable
proxy for the same underlying question, per llm-jury-eamt2026's finding
that ensembling 2+ distinct judges catches disagreement a single judge
hides, regardless of whether the judges share a vendor.

Samples ~20-30 (source, translation) pairs from:
  - experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json
    (per span x condition group, repeats[0]; cached gemini-3.1-flash-lite
    score reused from judge_scores_per_repeat, no re-call)
  - experiments/20260920_h_soft_anchor_disfluency_gate_replay.json
    (per span x condition group, repeats[0]; cached score reused from
    judge_scores)
  - experiments/20260918_h_backlog_compression_control_rpm9.json's
    top-level full_asr/full_translation (a third, unrelated-topic pair for
    variety; this file has no cached judge score, so one fresh
    gemini-3.1-flash-lite call is made here to keep both models scored on
    exactly the same text)

Only new API spend: the alt-Gemini-model calls (~20-30) plus the single
fresh gemini-3.1-flash-lite call noted above. Zero new Deepgram spend,
zero new translation generation.

Also computes same-model repeat noise (from the noisefloor file's own
2-repeat judge_scores_per_repeat) as a comparison noise floor, per
PLAYBOOK.md ANALYZE_RESULTS's standing rule not to treat a small
cross-condition score difference as a real effect without one.

Usage:
  python3 -m real_time_translation.experiments.judge_cross_gemini_model_replay
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from real_time_translation.experiments.results_io import last_cumulative_text
from real_time_translation.experiments.stats import mean, pearson_r, stdev

NOISEFLOOR_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)
DISFLUENCY_JSON = Path("experiments/20260920_h_soft_anchor_disfluency_gate_replay.json")
BACKLOG_JSON = Path("experiments/20260918_h_backlog_compression_control_rpm9.json")
OUT_JSON = Path("experiments/judge_cross_gemini_model_replay.json")

NOISEFLOOR_STRIDE = 24
DISFLUENCY_STRIDE = 24
PRIMARY_MODEL = "gemini-3.1-flash-lite"
ALT_MODEL_CANDIDATES = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]


def _judge_once(source: str, translation: str, model: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    from real_time_translation.experiments.translation_fidelity_judge import (
        _JUDGE_PROMPT,
    )

    prompt = _JUDGE_PROMPT.format(source=source, translation=translation)
    response = client.models.generate_content(model=model, contents=prompt)
    text = response.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Judge did not return JSON: {text[:300]!r}")
    return json.loads(match.group(0))


def pick_alt_model() -> tuple[str | None, str]:
    """Cheap availability check: a short generate_content call against each
    candidate in order. Returns (model_id_or_None, note)."""
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    notes = []
    for model in ALT_MODEL_CANDIDATES:
        try:
            client.models.generate_content(model=model, contents="ping")
            notes.append(f"{model}: OK")
            return model, "; ".join(notes)
        except Exception as e:  # noqa: BLE001 -- availability probe, not a crash
            notes.append(f"{model}: FAILED ({type(e).__name__}: {e})")
    return None, "; ".join(notes)


PairRow = tuple[str, str, int | None, str, str, float | None]


def _noisefloor_pairs(data: dict) -> list[PairRow]:
    """(source_file, group_key, span_index, source_full, translation,
    cached_primary_score) for every NOISEFLOOR_STRIDE'th span x condition
    group with a cached repeats[0] score."""
    rows: list[PairRow] = []
    groups: list[tuple[str, int, str, str, str, float]] = []
    by_source = data["results"]["by_source"]
    for source_name, source_val in by_source.items():
        for span in source_val["spans"]:
            source_full = span["source_full"]
            for cond, reps in span["condition_repeats"].items():
                if not reps:
                    continue
                score_entries = span["judge_scores_per_repeat"].get(cond) or []
                if not score_entries or score_entries[0] is None:
                    continue
                score = score_entries[0].get("score")
                if score is None:
                    continue
                translation = last_cumulative_text(reps[0])
                if not translation:
                    continue
                groups.append(
                    (
                        source_name,
                        span["span_index"],
                        cond,
                        source_full,
                        translation,
                        float(score),
                    )
                )
    for i in range(0, len(groups), NOISEFLOOR_STRIDE):
        source_name, span_index, cond, source_full, translation, score = groups[i]
        rows.append(
            (source_name, f"{cond}", span_index, source_full, translation, score)
        )
    return rows


def _disfluency_pairs(data: dict) -> list[PairRow]:
    rows: list[PairRow] = []
    groups: list[tuple[str, int, str, str, str, float]] = []
    by_source = data["results"]["by_source"]
    for source_name, source_val in by_source.items():
        for span in source_val["spans"]:
            source_full = span["source_full"]
            for cond, reps in span["condition_repeats"].items():
                if not reps:
                    continue
                score_entry = span["judge_scores"].get(cond)
                if score_entry is None:
                    continue
                score = score_entry.get("score")
                if score is None:
                    continue
                translation = last_cumulative_text(reps[0])
                if not translation:
                    continue
                groups.append(
                    (
                        source_name,
                        span["span_index"],
                        cond,
                        source_full,
                        translation,
                        float(score),
                    )
                )
    for i in range(0, len(groups), DISFLUENCY_STRIDE):
        source_name, span_index, cond, source_full, translation, score = groups[i]
        rows.append(
            (source_name, f"{cond}", span_index, source_full, translation, score)
        )
    return rows


def _backlog_pair(data: dict) -> PairRow:
    source_full = data["results"]["full_asr"]
    translation = data["results"]["full_translation"]
    return (
        data["experiment_name"],
        "full_run",
        None,
        source_full,
        translation,
        None,  # no cached score -- a fresh primary-model call is made for this one
    )


def same_model_repeat_noise(data: dict) -> dict:
    """Same-model (gemini-3.1-flash-lite) repeat-to-repeat noise, from the
    noisefloor file's own 2-repeat judge_scores_per_repeat, as a comparison
    floor for the cross-model score deltas below."""
    within_group_variances: list[float] = []
    by_source = data["results"]["by_source"]
    for source_val in by_source.values():
        for span in source_val["spans"]:
            for _cond, score_entries in span["judge_scores_per_repeat"].items():
                scores = [
                    e["score"]
                    for e in score_entries
                    if e and e.get("score") is not None
                ]
                if len(scores) < 2:
                    continue
                s = stdev([float(x) for x in scores])
                if s is not None:
                    within_group_variances.append(s**2)
    mean_var = mean(within_group_variances)
    return {
        "n_groups": len(within_group_variances),
        "mean_within_group_variance": mean_var,
        "within_group_noise_stdev": mean_var**0.5 if mean_var is not None else None,
        "notes": (
            "sqrt(mean(per-group variance)) of gemini-3.1-flash-lite's own "
            "repeat-to-repeat score noise on an identical (source, "
            "translation) pair, matching judge_noise_power_analysis.py's "
            "within-group convention. This is the floor any real cross-model "
            "disagreement should be judged against."
        ),
    }


def analyze(
    noisefloor_path: Path, disfluency_path: Path, backlog_path: Path
) -> dict:
    noisefloor_data = json.loads(noisefloor_path.read_text(encoding="utf-8"))
    disfluency_data = json.loads(disfluency_path.read_text(encoding="utf-8"))
    backlog_data = json.loads(backlog_path.read_text(encoding="utf-8"))

    alt_model, alt_model_probe_notes = pick_alt_model()
    if alt_model is None:
        return {
            "blocked_reason": (
                "No candidate alt-Gemini model is currently reachable: "
                f"{alt_model_probe_notes}"
            ),
            "alt_model_probe_notes": alt_model_probe_notes,
        }

    pairs = (
        _noisefloor_pairs(noisefloor_data)
        + _disfluency_pairs(disfluency_data)
        + [_backlog_pair(backlog_data)]
    )

    rows: list[dict] = []
    primary_scores: list[float] = []
    alt_scores: list[float] = []

    for (
        source_file,
        group_key,
        span_index,
        source_full,
        translation,
        cached_score,
    ) in pairs:
        if cached_score is None:
            primary_result = _judge_once(source_full, translation, PRIMARY_MODEL)
            primary_score = primary_result.get("score")
        else:
            primary_score = cached_score
        alt_result = _judge_once(source_full, translation, alt_model)
        alt_score = alt_result.get("score")

        if primary_score is None or alt_score is None:
            continue

        primary_score = float(primary_score)
        alt_score = float(alt_score)
        primary_scores.append(primary_score)
        alt_scores.append(alt_score)
        rows.append(
            {
                "source_file": source_file,
                "group_key": group_key,
                "span_index": span_index,
                "primary_model": PRIMARY_MODEL,
                "primary_score": primary_score,
                "primary_score_was_cached": cached_score is not None,
                "alt_model": alt_model,
                "alt_score": alt_score,
                "delta_alt_minus_primary": alt_score - primary_score,
            }
        )

    deltas = [r["delta_alt_minus_primary"] for r in rows]
    noise_floor = same_model_repeat_noise(noisefloor_data)

    return {
        "alt_model": alt_model,
        "alt_model_probe_notes": alt_model_probe_notes,
        "primary_model": PRIMARY_MODEL,
        "n_pairs": len(rows),
        "pearson_r": pearson_r(primary_scores, alt_scores),
        "mean_delta_alt_minus_primary": mean(deltas),
        "stdev_delta_alt_minus_primary": stdev(deltas),
        "mean_abs_delta": mean([abs(d) for d in deltas]) if deltas else None,
        "same_model_repeat_noise_floor": noise_floor,
        "rows": rows,
        "sources": {
            "noisefloor_replay": str(noisefloor_path),
            "disfluency_gate_replay": str(disfluency_path),
            "backlog_compression_control": str(backlog_path),
        },
        "notes": (
            "Cross-model (not cross-repeat) disagreement check: same fixed "
            "(source, translation) pair scored by two distinct Gemini "
            "models. A mean_abs_delta well above "
            "same_model_repeat_noise_floor.within_group_noise_stdev is "
            "evidence the single-judge-model design hides real disagreement "
            "an ensemble would catch; a delta within that floor is "
            "consistent with the two models simply being as noisy as one "
            "model judging itself twice. Report honestly either way -- this "
            "is a single-pass check (one alt-model call per pair), not a "
            "repeated-sampling design."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--noisefloor", type=Path, default=NOISEFLOOR_JSON)
    parser.add_argument("--disfluency", type=Path, default=DISFLUENCY_JSON)
    parser.add_argument("--backlog", type=Path, default=BACKLOG_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = analyze(args.noisefloor, args.disfluency, args.backlog)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

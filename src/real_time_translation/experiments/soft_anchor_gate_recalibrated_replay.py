"""h-soft-anchor-gate-recalibrated-noisefloor: follow-up to
h-soft-anchor-disfluency-gate (cycle 19), fixing two calibration problems
flagged in that hypothesis's own result_summary:

1. GATE_MIN_WORDS=5 gated far more batches than intended (40%/57% of
   eligible batches on the two clips) because the threshold was picked from
   a single quoted example rather than the actual delta_text word-count
   distribution. A histogram computed during this hypothesis's design over
   both source experiments' i>0 batches (145 eligible batches total) showed
   median delta_text length is only 4-5 words in both clips, so
   GATE_MIN_WORDS=5 was gating at-or-above the median. GATE_MIN_WORDS=2
   gates 10% (2/20) on the original clip and 16% (20/125) on the guest-talk
   clip -- a much more targeted gate that still catches the actual
   hallucination-triggering batch (guest-talk span 69, batch index 1,
   delta_text "AIM to", 2 words -- NOT the 9-word string quoted in
   h-soft-anchor-disfluency-gate's description, which was batch 0's delta).

2. The aggregate fidelity comparison in h-soft-anchor-disfluency-gate used
   judge() only once per condition per span (repeats[0]), and a
   same-code-path noise check (spans where gating had zero effect) showed
   judge-score swings of -30..+45, as large as or larger than the reported
   mean effects. This script calls judge() on every repeat (still
   REPEATS=2, so this doubles judge() calls but does not touch
   translate() call counts) and stores a list of scores per condition per
   span, so mean/stdev across repeats can be computed directly instead of
   inferred post-hoc.

Reuses prefix_lock_replay.py's _extract_spans()/_run_condition()/
_translate_once() and soft_anchor_disfluency_gate_replay.py's
_run_gated_condition() unchanged except for the module-level
GATE_MIN_WORDS this module sets before calling it.

Run against the same two already-recorded source experiments as the prior
two hypotheses in this line:
  - experiments/20260915_h_continuation_anchor_test_rpmunlimited.json
  - experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json

Deepgram is not needed (replay of already-recorded source deltas); only
LLMTranslator (Gemini translate + judge) calls are made.

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_gate_recalibrated_replay
"""

from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments import (
    soft_anchor_disfluency_gate_replay as gate_replay,
)
from real_time_translation.experiments.flicker_metrics import normalized_erasure
from real_time_translation.experiments.prefix_lock_replay import (
    _extract_spans,
    _run_condition,
)
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENTS = (
    Path("experiments/20260915_h_continuation_anchor_test_rpmunlimited.json"),
    Path("experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json"),
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path(
    "experiments/20260921_h_soft_anchor_gate_recalibrated_noisefloor_replay.json"
)

REPEATS = 2
CONDITIONS = ("baseline_replay", "soft_anchor_replay", "gated_soft_anchor")
GATE_MIN_WORDS = 2

# gate_replay._run_gated_condition() reads this module-level constant, so
# override it before calling into that function.
gate_replay.GATE_MIN_WORDS = GATE_MIN_WORDS


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


async def main() -> None:
    from real_time_translation.config import Config
    from real_time_translation.experiments.translation_fidelity_judge import judge

    config = Config.from_env(require_zoom=False)
    if not config.google_api_key:
        raise SystemExit("GOOGLE_API_KEY not set")

    by_source: dict[str, dict] = {}
    translator: LLMTranslator | None = None

    for source_path in SOURCE_EXPERIMENTS:
        source_data = json.loads(source_path.read_text(encoding="utf-8"))
        events = source_data["results"]["events"]
        src_models = source_data["models"]

        spans = _extract_spans(events)
        usable_spans = [
            s for s in spans if all(b.cumulative_text for b in s.batches)
        ]
        print(
            f"=== {source_path.name}: {len(spans)} spans, "
            f"{len(usable_spans)} usable ==="
        )

        if translator is None:
            translator = LLMTranslator(
                provider="gemini",
                api_key=config.google_api_key,
                model=src_models["llm_model"],
                dictionary_path=DICTIONARY_PATH,
                thinking_budget=src_models["gemini_thinking_budget"],
            )
            await translator.prepare()

        span_results: list[dict] = []
        for span in usable_spans:
            n_batches = len(span.batches)
            print(f"  -- span {span.span_index} ({n_batches} batches) --")
            condition_repeats: dict[str, list[list[str]]] = {
                c: [] for c in CONDITIONS
            }
            gated_indices: list[int] = []
            for condition in CONDITIONS:
                for r in range(REPEATS):
                    if condition == "gated_soft_anchor":
                        outputs, gated_indices = await gate_replay._run_gated_condition(
                            translator, span
                        )
                    else:
                        outputs = await _run_condition(translator, span, condition)
                    condition_repeats[condition].append(outputs)
                    print(f"    [{condition}] repeat {r}: {len(outputs)} outputs")

            condition_ne: dict[str, list[float | None]] = {}
            for condition, repeats in condition_repeats.items():
                nes = [normalized_erasure(outs) for outs in repeats if outs]
                condition_ne[condition] = nes

            source_full = span.batches[-1].cumulative_text

            # Call judge() on EVERY repeat (not just repeats[0]) so per-span
            # fidelity variance is measured directly instead of assumed away.
            judge_scores_per_repeat: dict[str, list[dict]] = {}
            for condition, repeats in condition_repeats.items():
                per_repeat: list[dict] = []
                for outputs in repeats:
                    translation_full = outputs[-1] if outputs else ""
                    try:
                        result = judge(source_full, translation_full)
                        per_repeat.append(
                            {
                                "score": result.get("score"),
                                "dropped_or_distorted": result.get(
                                    "dropped_or_distorted"
                                ),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        per_repeat.append(
                            {
                                "score": None,
                                "dropped_or_distorted": None,
                                "error": f"judge() failed: {type(exc).__name__}: {exc}",
                            }
                        )
                judge_scores_per_repeat[condition] = per_repeat

            judge_score_stats: dict[str, dict] = {}
            for condition, per_repeat in judge_scores_per_repeat.items():
                scores = [r["score"] for r in per_repeat if r["score"] is not None]
                judge_score_stats[condition] = {
                    "scores": scores,
                    "mean": _mean(scores),
                    "stdev": _stdev(scores),
                    "range": (min(scores), max(scores)) if scores else None,
                }

            span_results.append(
                {
                    "span_index": span.span_index,
                    "num_batches": n_batches,
                    "source_full": source_full,
                    "batches": [asdict(b) for b in span.batches],
                    "gated_batch_indices": gated_indices,
                    "condition_repeats": condition_repeats,
                    "condition_ne": condition_ne,
                    "judge_scores_per_repeat": judge_scores_per_repeat,
                    "judge_score_stats": judge_score_stats,
                }
            )

        aggregate = {}
        for condition in CONDITIONS:
            per_span_ne_means = []
            for sr in span_results:
                nes = [ne for ne in sr["condition_ne"][condition] if ne is not None]
                if nes:
                    per_span_ne_means.append(_mean(nes))
            all_repeat_scores = [
                s
                for sr in span_results
                for s in sr["judge_score_stats"][condition]["scores"]
            ]
            per_span_stdevs = [
                sr["judge_score_stats"][condition]["stdev"]
                for sr in span_results
                if sr["judge_score_stats"][condition]["stdev"] is not None
            ]
            aggregate[condition] = {
                "num_spans_with_defined_ne": len(per_span_ne_means),
                "mean_ne_across_spans": _mean(per_span_ne_means),
                "mean_fidelity_score_all_repeats": _mean(all_repeat_scores),
                "mean_within_span_fidelity_stdev": _mean(per_span_stdevs),
            }

        # Noise floor: same-code-path spans (zero batches actually gated by
        # gated_soft_anchor) isolate pure resampling variance, since
        # gated_soft_anchor behaves identically to soft_anchor_replay there.
        zero_effect_spans = [
            sr for sr in span_results if not sr["gated_batch_indices"]
        ]
        noise_floor_scores = [
            s
            for sr in zero_effect_spans
            for s in sr["judge_score_stats"]["gated_soft_anchor"]["scores"]
            + sr["judge_score_stats"]["soft_anchor_replay"]["scores"]
        ]
        noise_floor_stdev = _stdev(noise_floor_scores)

        gated_spans = [sr for sr in span_results if sr["gated_batch_indices"]]
        gated_vs_soft_anchor_diffs = [
            sr["judge_score_stats"]["gated_soft_anchor"]["mean"]
            - sr["judge_score_stats"]["soft_anchor_replay"]["mean"]
            for sr in gated_spans
            if sr["judge_score_stats"]["gated_soft_anchor"]["mean"] is not None
            and sr["judge_score_stats"]["soft_anchor_replay"]["mean"] is not None
        ]

        total_gated = sum(len(sr["gated_batch_indices"]) for sr in span_results)
        total_batches_i_gt_0 = sum(sr["num_batches"] - 1 for sr in span_results)

        by_source[source_path.name] = {
            "models": src_models,
            "spans": span_results,
            "aggregate": aggregate,
            "num_usable_spans": len(usable_spans),
            "num_gated_batches": total_gated,
            "num_eligible_batches": total_batches_i_gt_0,
            "noise_floor": {
                "num_zero_effect_spans": len(zero_effect_spans),
                "num_scores": len(noise_floor_scores),
                "stdev_across_same_code_path_resamples": noise_floor_stdev,
            },
            "gated_spans_comparison": {
                "num_gated_spans": len(gated_spans),
                "gated_vs_soft_anchor_mean_diffs": gated_vs_soft_anchor_diffs,
                "mean_of_diffs": _mean(gated_vs_soft_anchor_diffs),
            },
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_gate_recalibrated_noisefloor_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiments": [str(p) for p in SOURCE_EXPERIMENTS],
            "repeats_per_condition": REPEATS,
            "gate_min_words": GATE_MIN_WORDS,
            "note": (
                "Follow-up to h-soft-anchor-disfluency-gate (cycle 19, "
                "GATE_MIN_WORDS=5). Same deterministic replay setup "
                "(no live Deepgram, only Gemini translate calls; "
                "context_lines=[] and update_context=False for every "
                "call), but with GATE_MIN_WORDS=2 (recalibrated from an "
                "actual delta_text word-count histogram over both source "
                "experiments' i>0 batches -- median was 4-5 words, so "
                "GATE_MIN_WORDS=5 was gating at-or-above the median) and "
                "judge() called on EVERY repeat (not just repeats[0]) so "
                "per-span fidelity variance is measured directly."
            ),
        },
        "models": by_source[SOURCE_EXPERIMENTS[0].name]["models"],
        "results": {"by_source": by_source},
        "notes": (
            "h-soft-anchor-gate-recalibrated-noisefloor. Fixes two "
            "calibration problems h-soft-anchor-disfluency-gate's own "
            "result_summary flagged: GATE_MIN_WORDS chosen without a real "
            "word-count histogram, and aggregate fidelity comparisons "
            "based on a single judge() call per condition per span with "
            "no measured noise floor. See ANALYZE_RESULTS for the "
            "per-span judge-score spread on actually-gated spans "
            "(including guest-talk span 69) compared against the "
            "zero-effect-span noise floor computed here."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\nWrote {OUT_JSON}")
    for source_name, data in by_source.items():
        print(f"\n{source_name}:")
        print(
            f"  gated {data['num_gated_batches']}/{data['num_eligible_batches']} "
            "eligible (i>0) batches"
        )
        nf = data["noise_floor"]
        print(
            f"  noise floor stdev (zero-effect spans, n={nf['num_scores']}): "
            f"{nf['stdev_across_same_code_path_resamples']}"
        )
        for condition in CONDITIONS:
            a = data["aggregate"][condition]
            print(
                f"  {condition}: mean_ne={a['mean_ne_across_spans']} "
                f"(n={a['num_spans_with_defined_ne']} spans) "
                f"mean_fidelity_all_repeats={a['mean_fidelity_score_all_repeats']} "
                f"mean_within_span_stdev={a['mean_within_span_fidelity_stdev']}"
            )
        print(
            "  gated spans mean(gated_soft_anchor - soft_anchor_replay) "
            f"fidelity diff: {data['gated_spans_comparison']['mean_of_diffs']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

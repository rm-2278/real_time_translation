"""h-soft-anchor-gate-min-words-3: retest of the disfluency gate at
GATE_MIN_WORDS=3, following h-soft-anchor-gate-recalibrated-noisefloor
(cycle 20)'s discovery that GATE_MIN_WORDS=2 silently failed to gate its own
flagship case.

The gate condition in soft_anchor_disfluency_gate_replay.py is
`i > 0 and len(batch.delta_text.split()) < GATE_MIN_WORDS` (strict
less-than). With GATE_MIN_WORDS=2, a 2-word delta_text is NOT gated
(2 < 2 is False) -- confirmed directly against guest-talk span 69, batch
index 1, delta_text "AIM to" (2 words), the source of the "AIME"
hallucination that motivates this whole line of hypotheses:
cycle 20's own output JSON recorded gated_batch_indices: [] for span 69,
i.e. the gate never fired on its flagship case. With GATE_MIN_WORDS=3,
2 < 3 is True, so that batch is gated.

Identical structure to soft_anchor_gate_recalibrated_replay.py (reuses
prefix_lock_replay.py's _extract_spans()/_run_condition() and
soft_anchor_disfluency_gate_replay.py's _run_gated_condition() unchanged,
REPEATS=2 with judge() called on every repeat, not just repeats[0]) --
only GATE_MIN_WORDS and OUT_JSON differ.

Run against the same two already-recorded source experiments as the prior
hypotheses in this line:
  - experiments/20260915_h_continuation_anchor_test_rpmunlimited.json
  - experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json

Deepgram is not needed (replay of already-recorded source deltas); only
LLMTranslator (Gemini translate + judge) calls are made.

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_gate_min3_replay
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
OUT_JSON = Path("experiments/20260922_h_soft_anchor_gate_min3_replay.json")

REPEATS = 2
CONDITIONS = ("baseline_replay", "soft_anchor_replay", "gated_soft_anchor")
GATE_MIN_WORDS = 3

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

        # Flagship regression check: guest-talk span 69, batch index 1
        # ("AIM to", 2 words) must now actually be gated (2 < 3 is True),
        # unlike cycle 20's GATE_MIN_WORDS=2 run where it was not
        # (2 < 2 is False). Confirm directly against this run's own output
        # rather than assuming from the arithmetic alone.
        span_69 = next(
            (sr for sr in span_results if sr["span_index"] == 69), None
        )
        span_69_batch1_gated = (
            span_69 is not None and 1 in span_69["gated_batch_indices"]
        )

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
            "span_69_batch1_gated": span_69_batch1_gated,
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_gate_min3_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiments": [str(p) for p in SOURCE_EXPERIMENTS],
            "repeats_per_condition": REPEATS,
            "gate_min_words": GATE_MIN_WORDS,
            "note": (
                "Follow-up to h-soft-anchor-gate-recalibrated-noisefloor "
                "(cycle 20, GATE_MIN_WORDS=2), which discovered its own "
                "gate condition (`len(delta_text.split()) < GATE_MIN_WORDS`, "
                "strict less-than) never fired on its flagship case "
                "(guest-talk span 69 batch 1, delta_text 'AIM to', 2 words: "
                "2 < 2 is False). GATE_MIN_WORDS=3 makes 2 < 3 True. Same "
                "deterministic replay setup (no live Deepgram, only Gemini "
                "translate calls; context_lines=[] and update_context=False "
                "for every call), judge() called on EVERY repeat (not just "
                "repeats[0])."
            ),
        },
        "models": by_source[SOURCE_EXPERIMENTS[0].name]["models"],
        "results": {"by_source": by_source},
        "notes": (
            "h-soft-anchor-gate-min-words-3. Retest of the disfluency gate "
            "at GATE_MIN_WORDS=3 after cycle 20 found GATE_MIN_WORDS=2 "
            "silently failed to gate its own flagship case (span 69 batch "
            "1) due to the strict-< comparison. See ANALYZE_RESULTS for "
            "whether span 69 batch 1 is actually gated this time "
            "(span_69_batch1_gated field per source) and how gating rate / "
            "fidelity compare against the threshold=2 and threshold=5 runs."
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
        print(f"  span 69 batch 1 gated: {data.get('span_69_batch1_gated')}")
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

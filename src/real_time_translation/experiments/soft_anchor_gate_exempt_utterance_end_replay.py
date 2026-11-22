"""h-soft-anchor-gate-exempt-utterance-end: does exempting is_utterance_end
batches from the soft-anchor disfluency gate recover the gate's excess
normalized-erasure (NE) cost without a judge-score regression?

Direct follow-up to h-gate-utterance-end-temporal-blindness (cycle 77),
which confirmed the gate (`i > 0 and len(batch.delta_text.split()) <
GATE_MIN_WORDS`, soft_anchor_disfluency_gate_replay.py:83) fires on
is_utterance_end=true batches 98.6% of the time, and that
h_soft_anchor_gate_erasure_decomposition's excess NE (gated_soft_anchor
0.615 vs soft_anchor_replay 0.067, excess 0.549) is concentrated almost
entirely in that same bucket (0.618 vs 0.091). That hypothesis's own
offsetting-benefit check (unpaired whole-span judge_score_stats, +3.4,
within ~one noise stdev) was left INCONCLUSIVE, not ruled out -- so this
script adds a new `gated_soft_anchor_exempt_end` condition (gate becomes
`i > 0 and not batch.is_utterance_end and len(batch.delta_text.split()) <
GATE_MIN_WORDS`, GATE_MIN_WORDS=3 matching the current recalibrated
default in soft_anchor_gate_min3_replay.py) and actually tests exempting
it, with judge() called on EVERY repeat (not just repeats[0]) so a later
ANALYZE_RESULTS pass can do a real paired-per-span comparison instead of
unpaired aggregate means.

4-way comparison: baseline_replay, soft_anchor_replay, gated_soft_anchor
(unchanged, kept as the direct comparison point), gated_soft_anchor_exempt_end
(new). Also records, per span, which batches were gated by the OLD gate but
are NOT gated by the NEW (exempt) gate -- "formerly_gated_now_exempted" --
and computes per-transition normalized erasure (reusing
flicker_metrics._longest_common_prefix_len, same method as
soft_anchor_gate_erasure_decomposition.py) on exactly those transitions, so
prediction (a) (NE on those transitions should drop back toward
soft_anchor_replay's own baseline) can be checked directly against this
run's own data without a second retroactive pass.

Run against the same two already-recorded source experiments as every prior
hypothesis in this line:
  - experiments/20260915_h_continuation_anchor_test_rpmunlimited.json
  - experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json

Deepgram is not needed (replay of already-recorded source deltas); only
LLMTranslator (Gemini translate + judge) calls are made.

Usage:
  python3 -m real_time_translation.experiments.\
soft_anchor_gate_exempt_utterance_end_replay
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import (
    _longest_common_prefix_len,
    normalized_erasure,
)
from real_time_translation.experiments.prefix_lock_replay import (
    Span,
    _extract_spans,
    _run_condition,
    _translate_once,
)
from real_time_translation.experiments.stats import mean, stdev
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENTS = (
    Path("experiments/20260915_h_continuation_anchor_test_rpmunlimited.json"),
    Path("experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json"),
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path(
    "experiments/20261122_h_soft_anchor_gate_exempt_utterance_end_replay.json"
)

REPEATS = 2
CONDITIONS = (
    "baseline_replay",
    "soft_anchor_replay",
    "gated_soft_anchor",
    "gated_soft_anchor_exempt_end",
)
GATE_MIN_WORDS = 3


async def _run_gated_condition(
    translator: LLMTranslator, span: Span
) -> tuple[list[str], list[int]]:
    """Unchanged gate: `i > 0 and len(delta_text.split()) < GATE_MIN_WORDS`,
    no is_utterance_end exemption. Mirrors
    soft_anchor_disfluency_gate_replay._run_gated_condition()."""
    outputs: list[str] = []
    gated_indices: list[int] = []
    prev_full_output: str | None = None
    for i, batch in enumerate(span.batches):
        gated = i > 0 and len(batch.delta_text.split()) < GATE_MIN_WORDS
        if gated:
            gated_indices.append(i)
        out = await _translate_once(
            translator,
            text=batch.cumulative_text,
            prior_translation=None if (i == 0 or gated) else prev_full_output,
            delta_only=False,
        )
        prev_full_output = out
        outputs.append(out)
    return outputs, gated_indices


async def _run_gated_exempt_end_condition(
    translator: LLMTranslator, span: Span
) -> tuple[list[str], list[int]]:
    """New gate: adds `not batch.is_utterance_end` so an utterance-final
    batch with a short delta_text anchors instead of falling back to a
    from-scratch translation."""
    outputs: list[str] = []
    gated_indices: list[int] = []
    prev_full_output: str | None = None
    for i, batch in enumerate(span.batches):
        gated = (
            i > 0
            and not batch.is_utterance_end
            and len(batch.delta_text.split()) < GATE_MIN_WORDS
        )
        if gated:
            gated_indices.append(i)
        out = await _translate_once(
            translator,
            text=batch.cumulative_text,
            prior_translation=None if (i == 0 or gated) else prev_full_output,
            delta_only=False,
        )
        prev_full_output = out
        outputs.append(out)
    return outputs, gated_indices


def _per_transition_erasure(outs: list[str]) -> list[tuple[int, int]]:
    """[(erasure_chars, final_text_len)] for t=0..len(outs)-2, normalized
    against this REPEAT's own final (last-batch) text length -- same method
    as soft_anchor_gate_erasure_decomposition.py's helper of the same name."""
    if len(outs) < 2:
        return []
    final_len = len(outs[-1])
    result = []
    for t in range(len(outs) - 1):
        lcp = _longest_common_prefix_len(outs[t], outs[t + 1])
        erasure = len(outs[t]) - lcp
        result.append((erasure, final_len))
    return result


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
            # NOTE (found this cycle, 2026-11-22): src_models["llm_model"]
            # (gemini-3-flash-preview, what every prior script in this line
            # used) now 404s via the google-genai SDK's default API
            # version ("not found for API version v1" -- confirmed still
            # listed and reachable via a raw v1beta REST call, so this is
            # an SDK-default/model-retirement issue, not a key/reachability
            # problem). Falls back to config.gemini_model
            # (gemini-3.1-flash-lite, already confirmed reachable, already
            # the model translation_fidelity_judge.judge() hardcodes) so
            # this run can proceed at all. All 4 conditions in THIS run use
            # the same (new) model, so the internal A/B comparison stays
            # valid; only a comparison against this hypothesis's own
            # historical NE/fidelity numbers (collected under
            # gemini-3-flash-preview) is confounded by the model change --
            # flagged explicitly here and in result_summary, per
            # PLAYBOOK.md's ANALYZE_RESULTS config-diff discipline, rather
            # than silently treated as comparable.
            model_name = src_models["llm_model"]
            try:
                translator = LLMTranslator(
                    provider="gemini",
                    api_key=config.google_api_key,
                    model=model_name,
                    dictionary_path=DICTIONARY_PATH,
                    thinking_budget=src_models["gemini_thinking_budget"],
                )
                await translator.prepare()
                await _translate_once(
                    translator, text="hello", prior_translation=None, delta_only=False
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  model {model_name!r} unavailable ({type(exc).__name__}: "
                    f"{exc}); falling back to config.gemini_model "
                    f"{config.gemini_model!r}"
                )
                model_name = config.gemini_model
                translator = LLMTranslator(
                    provider="gemini",
                    api_key=config.google_api_key,
                    model=model_name,
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
            gated_indices_by_condition: dict[str, list[int]] = {
                "gated_soft_anchor": [],
                "gated_soft_anchor_exempt_end": [],
            }
            for condition in CONDITIONS:
                for r in range(REPEATS):
                    if condition == "gated_soft_anchor":
                        outputs, gated_indices = await _run_gated_condition(
                            translator, span
                        )
                        gated_indices_by_condition[condition] = gated_indices
                    elif condition == "gated_soft_anchor_exempt_end":
                        outputs, gated_indices = (
                            await _run_gated_exempt_end_condition(translator, span)
                        )
                        gated_indices_by_condition[condition] = gated_indices
                    else:
                        outputs = await _run_condition(translator, span, condition)
                    condition_repeats[condition].append(outputs)
                    print(f"    [{condition}] repeat {r}: {len(outputs)} outputs")

            condition_ne: dict[str, list[float | None]] = {}
            for condition, repeats in condition_repeats.items():
                nes = [normalized_erasure(outs) for outs in repeats if outs]
                condition_ne[condition] = nes

            source_full = span.batches[-1].cumulative_text

            # Call judge() on EVERY repeat (not just repeats[0]) per the
            # hypothesis's own paired-analysis requirement.
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
                    "mean": mean(scores),
                    "stdev": stdev(scores),
                    "range": (min(scores), max(scores)) if scores else None,
                }

            # Batches the OLD gate held back but the NEW (exempt) gate does
            # not -- exactly the is_utterance_end bucket this hypothesis
            # targets. Only meaningful where the old gate actually fired.
            formerly_gated_now_exempted = sorted(
                set(gated_indices_by_condition["gated_soft_anchor"])
                - set(gated_indices_by_condition["gated_soft_anchor_exempt_end"])
            )

            # Per-transition NE (reusing the erasure-decomposition method)
            # restricted to those specific transitions, for
            # gated_soft_anchor_exempt_end vs. gated_soft_anchor vs. the
            # soft_anchor_replay baseline -- directly tests prediction (a):
            # does NE on these transitions drop back toward baseline once
            # exempted?
            transition_ne_on_formerly_gated: dict[str, list[float]] = {
                c: []
                for c in (
                    "gated_soft_anchor",
                    "gated_soft_anchor_exempt_end",
                    "soft_anchor_replay",
                )
            }
            if formerly_gated_now_exempted:
                for condition in transition_ne_on_formerly_gated:
                    for outs in condition_repeats[condition]:
                        if not outs:
                            continue
                        per_t = _per_transition_erasure(outs)
                        for batch_idx in formerly_gated_now_exempted:
                            t = batch_idx - 1  # transition INTO batch_idx
                            if 0 <= t < len(per_t):
                                erasure, final_len = per_t[t]
                                if final_len > 0:
                                    transition_ne_on_formerly_gated[condition].append(
                                        erasure / final_len
                                    )

            span_results.append(
                {
                    "span_index": span.span_index,
                    "num_batches": n_batches,
                    "source_full": source_full,
                    "batches": [asdict(b) for b in span.batches],
                    "gated_batch_indices_by_condition": gated_indices_by_condition,
                    "formerly_gated_now_exempted_batch_indices": (
                        formerly_gated_now_exempted
                    ),
                    "condition_repeats": condition_repeats,
                    "condition_ne": condition_ne,
                    "judge_scores_per_repeat": judge_scores_per_repeat,
                    "judge_score_stats": judge_score_stats,
                    "transition_ne_on_formerly_gated_batches": {
                        c: {
                            "n": len(vals),
                            "mean": mean(vals),
                        }
                        for c, vals in transition_ne_on_formerly_gated.items()
                    },
                }
            )

        aggregate = {}
        for condition in CONDITIONS:
            per_span_ne_means = []
            for sr in span_results:
                nes = [ne for ne in sr["condition_ne"][condition] if ne is not None]
                if nes:
                    per_span_ne_means.append(mean(nes))
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
                "mean_ne_across_spans": mean(per_span_ne_means),
                "mean_fidelity_score_all_repeats": mean(all_repeat_scores),
                "mean_within_span_fidelity_stdev": mean(per_span_stdevs),
            }

        # Prediction (a): aggregate, across all spans, the per-transition NE
        # on formerly_gated_now_exempted batches for each of the 3 relevant
        # conditions. Expect gated_soft_anchor_exempt_end to sit close to
        # soft_anchor_replay and well below gated_soft_anchor on exactly
        # these transitions.
        formerly_gated_transition_ne: dict[str, list[float]] = {
            c: []
            for c in (
                "gated_soft_anchor",
                "gated_soft_anchor_exempt_end",
                "soft_anchor_replay",
            )
        }
        for sr in span_results:
            for c in formerly_gated_transition_ne:
                formerly_gated_transition_ne[c].extend(
                    [sr["transition_ne_on_formerly_gated_batches"][c]["mean"]]
                    if sr["transition_ne_on_formerly_gated_batches"][c]["mean"]
                    is not None
                    else []
                )
        formerly_gated_transition_ne_summary = {
            c: {"n_spans": len(vals), "mean_of_per_span_means": mean(vals)}
            for c, vals in formerly_gated_transition_ne.items()
        }

        # Noise floor: spans where gated_soft_anchor_exempt_end gates
        # nothing (same code path as soft_anchor_replay there) isolate pure
        # resampling variance, same method as soft_anchor_gate_min3_replay.
        zero_effect_spans = [
            sr
            for sr in span_results
            if not sr["gated_batch_indices_by_condition"][
                "gated_soft_anchor_exempt_end"
            ]
        ]
        noise_floor_scores = [
            s
            for sr in zero_effect_spans
            for s in sr["judge_score_stats"]["gated_soft_anchor_exempt_end"]["scores"]
            + sr["judge_score_stats"]["soft_anchor_replay"]["scores"]
        ]
        noise_floor_stdev = stdev(noise_floor_scores)

        # Prediction (b) inputs: per-span mean-score diffs, both vs.
        # soft_anchor_replay (consistency with the min3 line) and vs.
        # gated_soft_anchor (the actual improvement question this
        # hypothesis asks). ANALYZE_RESULTS is expected to decompose these
        # properly (paired, noise-floor-aware) rather than trust the raw
        # means here.
        gated_end_spans = [
            sr
            for sr in span_results
            if sr["gated_batch_indices_by_condition"]["gated_soft_anchor_exempt_end"]
        ]
        exempt_vs_replay_diffs = [
            sr["judge_score_stats"]["gated_soft_anchor_exempt_end"]["mean"]
            - sr["judge_score_stats"]["soft_anchor_replay"]["mean"]
            for sr in gated_end_spans
            if sr["judge_score_stats"]["gated_soft_anchor_exempt_end"]["mean"]
            is not None
            and sr["judge_score_stats"]["soft_anchor_replay"]["mean"] is not None
        ]
        exempt_vs_gated_diffs = [
            sr["judge_score_stats"]["gated_soft_anchor_exempt_end"]["mean"]
            - sr["judge_score_stats"]["gated_soft_anchor"]["mean"]
            for sr in gated_end_spans
            if sr["judge_score_stats"]["gated_soft_anchor_exempt_end"]["mean"]
            is not None
            and sr["judge_score_stats"]["gated_soft_anchor"]["mean"] is not None
        ]

        total_formerly_gated_now_exempted = sum(
            len(sr["formerly_gated_now_exempted_batch_indices"])
            for sr in span_results
        )
        total_gated_old = sum(
            len(sr["gated_batch_indices_by_condition"]["gated_soft_anchor"])
            for sr in span_results
        )
        total_gated_exempt = sum(
            len(sr["gated_batch_indices_by_condition"]["gated_soft_anchor_exempt_end"])
            for sr in span_results
        )
        total_batches_i_gt_0 = sum(sr["num_batches"] - 1 for sr in span_results)

        actual_models = dict(src_models)
        actual_models["llm_model"] = model_name
        actual_models["llm_model_original_in_source"] = src_models["llm_model"]
        by_source[source_path.name] = {
            "models": actual_models,
            "spans": span_results,
            "aggregate": aggregate,
            "num_usable_spans": len(usable_spans),
            "num_eligible_batches": total_batches_i_gt_0,
            "num_gated_batches_old_gate": total_gated_old,
            "num_gated_batches_exempt_gate": total_gated_exempt,
            "num_formerly_gated_now_exempted": total_formerly_gated_now_exempted,
            "formerly_gated_transition_ne_summary": (
                formerly_gated_transition_ne_summary
            ),
            "noise_floor": {
                "num_zero_effect_spans": len(zero_effect_spans),
                "num_scores": len(noise_floor_scores),
                "stdev_across_same_code_path_resamples": noise_floor_stdev,
            },
            "gated_end_spans_comparison": {
                "num_gated_end_spans": len(gated_end_spans),
                "exempt_vs_replay_mean_diffs": exempt_vs_replay_diffs,
                "mean_of_exempt_vs_replay_diffs": mean(exempt_vs_replay_diffs),
                "exempt_vs_gated_mean_diffs": exempt_vs_gated_diffs,
                "mean_of_exempt_vs_gated_diffs": mean(exempt_vs_gated_diffs),
            },
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_gate_exempt_utterance_end_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiments": [str(p) for p in SOURCE_EXPERIMENTS],
            "repeats_per_condition": REPEATS,
            "gate_min_words": GATE_MIN_WORDS,
            "note": (
                "Follow-up to h-gate-utterance-end-temporal-blindness (cycle "
                "77) and h-soft-anchor-gate-erasure-decomposition, which "
                "found the disfluency gate fires on is_utterance_end=true "
                "batches 98.6% of the time and that almost all of "
                "gated_soft_anchor's excess NE sits in that bucket. Adds "
                "gated_soft_anchor_exempt_end (gate: `i > 0 and not "
                "batch.is_utterance_end and len(delta_text.split()) < "
                f"{GATE_MIN_WORDS}`) alongside the unchanged gated_soft_anchor "
                "for direct comparison. judge() called on EVERY repeat (not "
                "just repeats[0]). No live Deepgram -- only Gemini translate "
                "+ judge calls, replaying already-recorded source deltas. "
                "MODEL NOTE: the source experiments' own llm_model "
                "(gemini-3-flash-preview) 404s via this environment's "
                "google-genai SDK as of 2026-11-22 (model retirement/SDK-"
                "default drift, not a reachability/key problem -- confirmed "
                "still listed by ListModels and reachable via a raw v1beta "
                "REST call). This script falls back to config.gemini_model "
                "(see each source's models.llm_model vs. "
                "models.llm_model_original_in_source) for ALL FOUR "
                "conditions equally, so the within-run A/B comparison stays "
                "valid; only a comparison against this hypothesis line's "
                "own historical NE/fidelity numbers (collected under "
                "gemini-3-flash-preview) is confounded by the model change."
            ),
        },
        "models": by_source[SOURCE_EXPERIMENTS[0].name]["models"],
        "results": {"by_source": by_source},
        "notes": (
            "h-soft-anchor-gate-exempt-utterance-end. See "
            "'formerly_gated_transition_ne_summary' per source for "
            "prediction (a) (does NE on formerly-gated is_utterance_end "
            "transitions drop back toward soft_anchor_replay's level once "
            "exempted?) and 'gated_end_spans_comparison' for the raw inputs "
            "to prediction (b) (paired per-span judge-score diff, exempt "
            "vs. gated_soft_anchor and vs. soft_anchor_replay) -- "
            "ANALYZE_RESULTS should decompose these against the noise floor "
            "per PLAYBOOK.md's rule rather than trust the unpaired/raw "
            "means directly."
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
            f"  old gate: {data['num_gated_batches_old_gate']}/"
            f"{data['num_eligible_batches']} eligible batches gated"
        )
        print(
            f"  exempt gate: {data['num_gated_batches_exempt_gate']}/"
            f"{data['num_eligible_batches']} eligible batches gated"
        )
        print(
            "  formerly-gated-now-exempted batches: "
            f"{data['num_formerly_gated_now_exempted']}"
        )
        print(
            "  formerly-gated transition NE summary: "
            f"{data['formerly_gated_transition_ne_summary']}"
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
        gc = data["gated_end_spans_comparison"]
        print(
            "  gated-end spans mean(exempt_end - soft_anchor_replay) fidelity "
            f"diff: {gc['mean_of_exempt_vs_replay_diffs']}"
        )
        print(
            "  gated-end spans mean(exempt_end - gated_soft_anchor) fidelity "
            f"diff: {gc['mean_of_exempt_vs_gated_diffs']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

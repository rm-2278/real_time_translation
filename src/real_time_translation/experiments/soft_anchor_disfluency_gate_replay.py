"""h-soft-anchor-disfluency-gate: does gating soft_anchor_replay's
continuation-anchor prompt off for very short/fragmentary per-batch source
deltas avoid the hallucination-under-disfluency failure mode
h-soft-anchor-generalization-guest-talk found (span 69 of the guest-talk
clip: delta_text "our paper, we just like a like like a AIM to" -> the
anchor fabricated a specific but unsupported "AIME" expansion to make the
cut-off fragment cohere with its own prior translation) while keeping most
of the cross-batch NE win that motivated soft_anchor_replay in the first
place (h-hard-prefix-lock-continuation: 0.597->0.212 mean NE; confirmed to
generalize in h-soft-anchor-generalization-guest-talk, 83% relative
reduction on the guest-talk clip).

Adds a `gated_soft_anchor` condition: for batch i>0, if
len(delta_text.split()) < GATE_MIN_WORDS, fall back to a from-scratch
baseline translation for THAT batch only (prior_translation=None), but
still update prev_full_output from the produced output so a later, longer
batch in the same span can still anchor off it. GATE_MIN_WORDS=5 is a first
cut -- span 69's flagged delta_text has 9 words but is still a cut-off
fragment ending mid-clause, so the threshold may need revisiting; recorded
per-batch gate decisions below let the analysis check this directly instead
of assuming the threshold is right.

Reuses prefix_lock_replay.py's _extract_spans()/_translate_once() and its
existing baseline_replay/soft_anchor_replay conditions via _run_condition()
unchanged; gated_soft_anchor is implemented locally here since it needs to
also record which batches were gated. hard_lock is intentionally excluded
-- already conclusively tested and not recommended (h-hard-prefix-lock-
continuation).

Run against BOTH source experiments already used by the two prior
hypotheses in this line:
  - experiments/20260915_h_continuation_anchor_test_rpmunlimited.json
    (15 spans, the original clean monologue clip)
  - experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json
    (83 spans, the guest-talk clip where the failure mode was found)

Deepgram is not needed (replay of already-recorded source deltas); only
LLMTranslator (Gemini) calls are made.

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_disfluency_gate_replay
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import normalized_erasure
from real_time_translation.experiments.prefix_lock_replay import (
    Span,
    _extract_spans,
    _run_condition,
    _translate_once,
)
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENTS = (
    Path("experiments/20260915_h_continuation_anchor_test_rpmunlimited.json"),
    Path("experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json"),
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20260920_h_soft_anchor_disfluency_gate_replay.json")

REPEATS = 2
CONDITIONS = ("baseline_replay", "soft_anchor_replay", "gated_soft_anchor")
GATE_MIN_WORDS = 5


async def _run_gated_condition(
    translator: LLMTranslator, span: Span
) -> tuple[list[str], list[int]]:
    """Mirrors _run_condition()'s soft_anchor_replay loop, but gates the
    anchor off (prior_translation=None) for any batch i>0 whose delta_text
    has fewer than GATE_MIN_WORDS words. Returns (outputs, gated_indices)."""
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
                        outputs, gated_indices = await _run_gated_condition(
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
            judge_scores: dict[str, dict] = {}
            for condition, repeats in condition_repeats.items():
                translation_full = repeats[0][-1] if repeats[0] else ""
                try:
                    judge_scores[condition] = judge(source_full, translation_full)
                except Exception as exc:  # noqa: BLE001
                    judge_scores[condition] = {
                        "score": None,
                        "dropped_or_distorted": None,
                        "notes": f"judge() failed: {type(exc).__name__}: {exc}",
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
                    "judge_scores": {
                        c: {
                            "score": v.get("score"),
                            "dropped_or_distorted": v.get("dropped_or_distorted"),
                        }
                        for c, v in judge_scores.items()
                    },
                }
            )

        def _mean(values: list[float]) -> float | None:
            return sum(values) / len(values) if values else None

        aggregate = {}
        for condition in CONDITIONS:
            per_span_means = []
            for sr in span_results:
                nes = [ne for ne in sr["condition_ne"][condition] if ne is not None]
                if nes:
                    per_span_means.append(_mean(nes))
            judge_scores_all = [
                sr["judge_scores"][condition]["score"]
                for sr in span_results
                if sr["judge_scores"][condition].get("score") is not None
            ]
            aggregate[condition] = {
                "num_spans_with_defined_ne": len(per_span_means),
                "mean_ne_across_spans": _mean(per_span_means),
                "mean_fidelity_score": _mean(judge_scores_all),
            }

        total_gated = sum(len(sr["gated_batch_indices"]) for sr in span_results)
        total_batches_i_gt_0 = sum(sr["num_batches"] - 1 for sr in span_results)

        by_source[source_path.name] = {
            "models": src_models,
            "spans": span_results,
            "aggregate": aggregate,
            "num_usable_spans": len(usable_spans),
            "num_gated_batches": total_gated,
            "num_eligible_batches": total_batches_i_gt_0,
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_disfluency_gate_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiments": [str(p) for p in SOURCE_EXPERIMENTS],
            "repeats_per_condition": REPEATS,
            "gate_min_words": GATE_MIN_WORDS,
            "note": (
                "Deterministic replay over already-recorded per-batch source "
                "deltas (same _extract_spans() adaptive cumulative-vs-delta "
                "handling as prefix_lock_replay.py) -- no live Deepgram, only "
                "Gemini translate calls. context_lines=[] and "
                "update_context=False for every call (spans tested in "
                "isolation, no cross-utterance context). gated_soft_anchor "
                "falls back to a from-scratch (prior_translation=None) "
                "translation for any batch i>0 with fewer than "
                f"{GATE_MIN_WORDS} words of new delta_text, otherwise behaves "
                "identically to soft_anchor_replay. hard_lock intentionally "
                "omitted -- already conclusively tested and not recommended "
                "in h-hard-prefix-lock-continuation."
            ),
        },
        "models": by_source[SOURCE_EXPERIMENTS[0].name]["models"],
        "results": {"by_source": by_source},
        "notes": (
            "h-soft-anchor-disfluency-gate. Follow-up to "
            "h-soft-anchor-generalization-guest-talk, which found the "
            "cross-batch NE win generalizes but with a fidelity cost "
            "concentrated in disfluent/fragmentary short deltas (mean "
            "fidelity -3.9 on the guest-talk clip vs -0.6 on the original "
            "clip), including one hallucination (span 69, fabricated 'AIME' "
            "expansion). Tests whether a simple per-batch word-count gate on "
            "delta_text recovers baseline-like fidelity on short/fragmentary "
            "batches while retaining most of the NE reduction on the "
            "majority of (non-short) batches."
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
        for condition in CONDITIONS:
            a = data["aggregate"][condition]
            print(
                f"  {condition}: mean_ne={a['mean_ne_across_spans']} "
                f"(n={a['num_spans_with_defined_ne']} spans) "
                f"mean_fidelity={a['mean_fidelity_score']}"
            )


if __name__ == "__main__":
    asyncio.run(main())

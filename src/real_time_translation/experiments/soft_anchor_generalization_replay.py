"""h-soft-anchor-generalization-guest-talk: does the cross-batch NE
reduction from h-hard-prefix-lock-continuation's soft_anchor_replay
condition (0.597->0.212 mean NE across 15 spans, see
experiments/20260919_h_hard_prefix_lock_replay.json) generalize beyond the
one clip it was measured on?

Reuses prefix_lock_replay.py's span-extraction and per-condition replay
logic unchanged (same _extract_spans()/_translate_once()/_run_condition(),
same adaptive cumulative-vs-delta original_text handling documented
there) against a different, larger source experiment:
experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json (83 usable
multi-batch spans / 208 total batches, vs the original 15 spans -- a guest
interview rather than the original clip's monologue, so a structurally
different source too).

CONDITIONS is deliberately narrowed to (baseline_replay, soft_anchor_replay)
only -- hard_lock was already conclusively tested and found not
recommended (real fidelity cost from mid-clause frozen-prefix joins,
99.3->90.7 mean judge score); re-running it here would not answer a new
question and would needlessly ~1.5x the API spend.

Deepgram is not needed (replay of already-recorded source deltas); only
LLMTranslator (Gemini) calls are made.

Usage:
  python3 -m real_time_translation.experiments.soft_anchor_generalization_replay
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import normalized_erasure
from real_time_translation.experiments.prefix_lock_replay import (
    _extract_spans,
    _run_condition,
)
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENT = Path(
    "experiments/20260915_llm_course_ep8_guest_talk_10min_rpm60.json"
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20260919_h_soft_anchor_generalization_guest_talk.json")

REPEATS = 2
CONDITIONS = ("baseline_replay", "soft_anchor_replay")


async def main() -> None:
    from real_time_translation.config import Config
    from real_time_translation.experiments.translation_fidelity_judge import judge

    config = Config.from_env(require_zoom=False)
    if not config.google_api_key:
        raise SystemExit("GOOGLE_API_KEY not set")

    source_data = json.loads(SOURCE_EXPERIMENT.read_text(encoding="utf-8"))
    events = source_data["results"]["events"]
    src_models = source_data["models"]

    spans = _extract_spans(events)
    print(f"Found {len(spans)} multi-batch spans in {SOURCE_EXPERIMENT}")
    usable_spans = [s for s in spans if all(b.cumulative_text for b in s.batches)]
    print(f"Usable spans (non-empty cumulative text): {len(usable_spans)}")

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
        print(f"=== span {span.span_index} ({n_batches} batches) ===")
        condition_repeats: dict[str, list[list[str]]] = {c: [] for c in CONDITIONS}
        for condition in CONDITIONS:
            for r in range(REPEATS):
                outputs = await _run_condition(translator, span, condition)
                condition_repeats[condition].append(outputs)
                print(f"  [{condition}] repeat {r}: {len(outputs)} batch outputs")

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

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "h_soft_anchor_generalization_guest_talk",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "repeats_per_condition": REPEATS,
            "note": (
                "Deterministic replay over already-recorded per-batch source "
                "deltas (same _extract_spans() adaptive cumulative-vs-delta "
                "handling as prefix_lock_replay.py) -- no live Deepgram, "
                "only Gemini translate calls. context_lines=[] and "
                "update_context=False for every call (spans tested in "
                "isolation, no cross-utterance context). hard_lock condition "
                "intentionally omitted -- already conclusively tested and "
                "not recommended in h-hard-prefix-lock-continuation."
            ),
        },
        "models": src_models,
        "results": {"spans": span_results},
        "aggregate": aggregate,
        "notes": (
            "h-soft-anchor-generalization-guest-talk. Follow-up to "
            "h-hard-prefix-lock-continuation, checking whether that "
            "hypothesis's soft_anchor_replay NE reduction (0.597->0.212 "
            "mean across 15 spans in "
            "experiments/20260919_h_hard_prefix_lock_replay.json) "
            "generalizes to a second, larger (83-span), structurally "
            "different (guest interview vs monologue) source clip."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\nWrote {OUT_JSON}")
    for condition in CONDITIONS:
        a = aggregate[condition]
        print(
            f"{condition}: mean_ne={a['mean_ne_across_spans']} "
            f"(n={a['num_spans_with_defined_ne']} spans) "
            f"mean_fidelity={a['mean_fidelity_score']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

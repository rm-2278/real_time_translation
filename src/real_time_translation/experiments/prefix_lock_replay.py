"""h-hard-prefix-lock-continuation: does mechanically freezing the
already-committed translation prefix on a continuation batch (translating
only the new source delta and concatenating) reduce cross-batch translation
flicker more than h-continuation-context-anchor's soft advisory anchor did?

Grounded in lacuna2026-beam-search-cascade-flicker (Interspeech 2024): that
paper gets a 20+% flicker reduction by reusing MT beam-search state across
incremental ASR updates. This repo's translator is API-based (no beam
state), so this replays a prompting-level approximation: the *frozen
prefix* condition below never asks the model to reproduce prior output --
it mechanically concatenates it, guaranteeing zero rewriting of the frozen
part by construction (the true open question is whether the resulting
Japanese reads naturally / stays faithful, not whether NE drops).

Design note (found this session): `TimedEvent.original_text` on
translation_complete events is NOT reliably cumulative across a multi-batch
span. Checked across all 47 experiment JSONs: of 1591 consecutive
same-utterance batch transitions, only 1213 (76%) have the later batch's
original_text starting with the earlier batch's original_text as a literal
prefix -- the other 378 (24%) show no overlap at all, i.e. original_text is
sometimes the per-batch new-fragment delta and sometimes the full
accumulated source, inconsistently. Root cause not fully traced (plausibly
async utterance_id/`_utterance_source_text` bookkeeping in
pipeline.py's `_translation_worker`/`_emit_batch_result`, not reproducible
from a static read alone) -- flagged as a new near-miss for
PLAYBOOK.md's "check actual field construction, don't assume uniform
population" rule, see reflections.md. Practical fix used here: adaptively
detect prefix-containment per consecutive pair and extract the true delta
either way (strip the shared prefix when present, else treat the whole
value as the delta), then rebuild a self-consistent cumulative source per
batch from those deltas -- independent of which raw form the logged field
happened to take.

Every translate call in this script uses context_lines=[] (empty) and
update_context=False (stateless), so spans are tested in isolation with no
cross-utterance context -- this hypothesis is about cross-*batch* (within
an utterance) continuity, not cross-utterance context, so that's an
intentionally-fixed variable, not a missing feature.

Deepgram is not needed (replay of already-recorded source deltas from
experiments/20260915_h_continuation_anchor_test_rpmunlimited.json); only
LLMTranslator (Gemini) calls are made.

Usage:
  python3 -m real_time_translation.experiments.prefix_lock_replay
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.flicker_metrics import normalized_erasure
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENT = Path(
    "experiments/20260915_h_continuation_anchor_test_rpmunlimited.json"
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20260919_h_hard_prefix_lock_replay.json")

REPEATS = 2
CONDITIONS = ("baseline_replay", "soft_anchor_replay", "hard_lock")


@dataclass
class BatchSource:
    key: tuple[float, float]
    delta_text: str
    cumulative_text: str
    is_utterance_end: bool


@dataclass
class Span:
    span_index: int
    batches: list[BatchSource]


def _extract_spans(events: list[dict]) -> list[Span]:
    """Rebuild multi-batch utterance spans with a self-consistent per-batch
    delta/cumulative English source, adaptively handling the
    cumulative-vs-delta original_text inconsistency documented above."""
    raw_batches: list[tuple[tuple[float, float], str, bool]] = []
    current_key: tuple[float, float] | None = None
    current_orig = ""
    current_end = True
    for e in events:
        if e.get("kind") != "translation_complete":
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if key == current_key:
            continue  # duplicate translation_complete for the same batch key
        current_key = key
        current_orig = e.get("original_text", "")
        current_end = e.get("is_utterance_end", True)
        raw_batches.append((current_key, current_orig, current_end))

    spans: list[Span] = []
    span_raw: list[tuple[tuple[float, float], str, bool]] = []

    def flush_span() -> None:
        if len(span_raw) < 2:
            return
        batches: list[BatchSource] = []
        cumulative = ""
        prev_orig = ""
        for key, orig, end in span_raw:
            if cumulative and orig.startswith(prev_orig) and prev_orig:
                delta = orig[len(prev_orig) :].strip()
            else:
                delta = orig.strip()
            cumulative = f"{cumulative} {delta}".strip() if cumulative else delta
            batches.append(
                BatchSource(
                    key=key,
                    delta_text=delta,
                    cumulative_text=cumulative,
                    is_utterance_end=end,
                )
            )
            prev_orig = orig
        spans.append(Span(span_index=len(spans), batches=batches))

    prev_ended = True
    for key, orig, end in raw_batches:
        if prev_ended:
            flush_span()
            span_raw = []
        span_raw.append((key, orig, end))
        prev_ended = end
    flush_span()
    return spans


async def _translate_once(
    translator: LLMTranslator,
    *,
    text: str,
    prior_translation: str | None,
    delta_only: bool,
) -> str:
    if not text.strip():
        return ""
    full = ""
    async for chunk in translator.translate_stream(
        text,
        context_lines=[],
        update_context=False,
        prior_translation=prior_translation,
        delta_only=delta_only,
    ):
        full += chunk
    return full.strip()


async def _run_condition(
    translator: LLMTranslator, span: Span, condition: str
) -> list[str]:
    """Returns the ordered list of each batch's final displayed text for
    this condition (what a viewer would see on screen after that batch)."""
    outputs: list[str] = []
    frozen_prefix = ""
    prev_full_output: str | None = None
    for i, batch in enumerate(span.batches):
        if condition == "baseline_replay":
            out = await _translate_once(
                translator,
                text=batch.cumulative_text,
                prior_translation=None,
                delta_only=False,
            )
        elif condition == "soft_anchor_replay":
            out = await _translate_once(
                translator,
                text=batch.cumulative_text,
                prior_translation=prev_full_output if i > 0 else None,
                delta_only=False,
            )
            prev_full_output = out
        elif condition == "hard_lock":
            if i == 0:
                out = await _translate_once(
                    translator,
                    text=batch.delta_text,
                    prior_translation=None,
                    delta_only=False,
                )
                frozen_prefix = out
            else:
                delta_out = await _translate_once(
                    translator,
                    text=batch.delta_text,
                    prior_translation=frozen_prefix,
                    delta_only=True,
                )
                out = frozen_prefix + delta_out
                frozen_prefix = out
        else:
            raise ValueError(condition)
        outputs.append(out)
    return outputs


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
    # Only spans with a defined baseline NE are analytically useful (a span
    # whose final batch's cumulative text is empty makes NE undefined) --
    # matches h-cross-utterance-flicker's own None-NE spans, an unrelated,
    # pre-existing data-quality quirk this hypothesis isn't trying to fix.
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
            # Every condition's last batch output is already the full
            # coherent translation up to that point (hard_lock's own output
            # per batch already has the frozen prefix concatenated in) --
            # never join across batches, that would duplicate the prefix.
            translation_full = repeats[0][-1] if repeats[0] else ""
            try:
                judge_scores[condition] = judge(source_full, translation_full)
            except Exception as exc:  # noqa: BLE001
                # translation_fidelity_judge.judge() has a known JSON-parse
                # fragility on some judge responses (pre-existing, not
                # introduced here) -- don't let one bad judge call abort an
                # otherwise-successful span's NE results.
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
        "experiment_name": "h_hard_prefix_lock_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "repeats_per_condition": REPEATS,
            "note": (
                "Deterministic replay over already-recorded per-batch source "
                "deltas (adaptively re-derived from original_text, see module "
                "docstring for the cumulative-vs-delta inconsistency found "
                "this session) -- no live Deepgram, only Gemini translate "
                "calls. context_lines=[] and update_context=False for every "
                "call (spans tested in isolation, no cross-utterance context)."
            ),
        },
        "models": src_models,
        "results": {"spans": span_results},
        "aggregate": aggregate,
        "notes": (
            "h-hard-prefix-lock-continuation. Grounded in "
            "lacuna2026-beam-search-cascade-flicker. Compares against "
            "h-continuation-context-anchor's live single-run soft-anchor "
            "result (translation_ne_char_cross_batch_mean 0.836->0.972, "
            "inconclusive/noisy) with a repeated, deterministic replay of "
            "all three conditions (baseline_replay, soft_anchor_replay, "
            "hard_lock) on the same source spans."
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

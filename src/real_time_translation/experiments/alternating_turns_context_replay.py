"""h-alternating-turn-context-format: does presenting prior source/
translation pairs as one chronologically-interleaved <history> block (new
alternating_turns_block() in prompts.py, gated behind llm_translator.py's
new `history_pairs` parameter) change translation continuity versus the
current two-separate-flat-blocks format (<context> source-only +
<previous_translation> target-only)?

Grounded in huang2024-conversational-simulmt's dialogue-style context
framing (prompt-shape only -- that paper's own fine-tuned, KV-cache-reusing
decoder mechanism cannot be replicated against this repo's hosted-API-only
Gemini calls; see papers.json's extraction_note for that caveat).

Deepgram's listen-websocket is confirmed blocked again this cycle (same
proxy WS-upgrade-mangling issue as every prior cycle, see PLAYBOOK.md), so
this replays the already-recorded, matched (source, translation) pairs from
experiments/20260915_h_continuation_anchor_test_rpmunlimited.json (15
segments of one utterance, asr+translation already paired per segment)
through the real LLMTranslator, sequentially/single-threaded, under two
conditions:

  baseline_replay: context_lines=<prior source lines>,
                   previous_translations=<prior translation lines>
                   (today's default two-flat-blocks format)
  alternating_turns: history_pairs=<(source, translation) pairs>
                      (the new interleaved <history> block)

Both conditions translate the SAME target text with the SAME prior window
(size 3, matching LLMTranslator's own default context_window_size) -- the
prompt shape is the only variable under test. Each condition is run
REPEATS=2 times (full independent passes) so a same-condition-twice noise
check (reflections.md cycle 19-21's method) can be done before trusting any
difference between conditions.

Usage:
  python3 -m real_time_translation.experiments.alternating_turns_context_replay
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENT = Path(
    "experiments/20260915_h_continuation_anchor_test_rpmunlimited.json"
)
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20261030_h_alternating_turn_context_replay.json")
CONTEXT_WINDOW = 3
REPEATS = 2


@dataclass
class SegmentResult:
    index: int
    source_text: str
    translation: str
    ttft_seconds: float | None
    total_seconds: float
    error: str | None = None


async def replay_once(
    *,
    label: str,
    segments: list[dict],
    api_key: str,
    model: str,
    thinking_budget: int | None,
    use_history_pairs: bool,
) -> list[SegmentResult]:
    """One full pass over all segments under one condition.

    Context/history for segment i is built from the already-recorded
    asr/translation of segments[max(0, i-CONTEXT_WINDOW):i] -- the real,
    previously-recorded text, not this run's own (possibly slightly
    different, due to sampling) output, so every repeat of every condition
    sees an identical prior-context window. Only `text` (segments[i]'s own
    asr) is newly translated each time.
    """
    translator = LLMTranslator(
        provider="gemini",
        api_key=api_key,
        model=model,
        dictionary_path=DICTIONARY_PATH,
        thinking_budget=thinking_budget,
    )
    await translator.prepare()

    results: list[SegmentResult] = []
    for i, seg in enumerate(segments):
        text = seg["asr"].strip()
        if not text:
            continue
        window = segments[max(0, i - CONTEXT_WINDOW) : i]
        t0 = time.time()
        ttft: float | None = None
        full = ""
        error: str | None = None
        try:
            if use_history_pairs:
                pairs = [(w["asr"], w["translation"]) for w in window]
                stream = translator.translate_stream(
                    text, history_pairs=pairs, update_context=False
                )
            else:
                context_lines = [w["asr"] for w in window]
                previous_translations = [w["translation"] for w in window]
                stream = translator.translate_stream(
                    text,
                    context_lines=context_lines,
                    previous_translations=previous_translations,
                    update_context=False,
                )
            async for chunk in stream:
                if ttft is None:
                    ttft = time.time() - t0
                full += chunk
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        results.append(
            SegmentResult(
                index=i,
                source_text=text,
                translation=full.strip(),
                ttft_seconds=ttft,
                total_seconds=time.time() - t0,
                error=error,
            )
        )
    print(f"  [{label}] done: {len(results)} segments")
    return results


async def main() -> None:
    from real_time_translation.config import Config
    from real_time_translation.experiments.translation_fidelity_judge import judge

    config = Config.from_env(require_zoom=False)
    if not config.google_api_key:
        raise SystemExit("GOOGLE_API_KEY not set")

    source_data = json.loads(SOURCE_EXPERIMENT.read_text(encoding="utf-8"))
    segments = source_data["results"]["segments"]
    src_models = source_data["models"]
    full_asr = source_data["results"]["full_asr"]

    conditions = {
        "baseline_replay": False,
        "alternating_turns": True,
    }

    all_repeats: dict[str, list[list[SegmentResult]]] = {c: [] for c in conditions}
    for label, use_history_pairs in conditions.items():
        print(f"=== {label} ===")
        for rep in range(REPEATS):
            print(f" repeat {rep + 1}/{REPEATS}")
            res = await replay_once(
                label=f"{label}#{rep}",
                segments=segments,
                api_key=config.google_api_key,
                model=src_models["llm_model"],
                thinking_budget=src_models.get("gemini_thinking_budget"),
                use_history_pairs=use_history_pairs,
            )
            all_repeats[label].append(res)

    print("Running fidelity judge (retention score vs English source) per repeat...")
    judge_scores: dict[str, list[float | None]] = {c: [] for c in conditions}
    full_translations: dict[str, list[str]] = {c: [] for c in conditions}
    for label, repeats in all_repeats.items():
        for rep_idx, results in enumerate(repeats):
            full_t = "".join(r.translation for r in results if not r.error)
            full_translations[label].append(full_t)
            jr = judge(full_asr, full_t)
            score = jr.get("score")
            judge_scores[label].append(score)
            print(f"  [{label}#{rep_idx}] retention_score={score}")

    def _mean(xs: list[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return sum(vals) / len(vals) if vals else None

    def _stdev(xs: list[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return statistics.stdev(vals) if len(vals) > 1 else None

    metrics = {}
    for label, repeats in all_repeats.items():
        ttfts = [
            r.ttft_seconds
            for results in repeats
            for r in results
            if r.ttft_seconds is not None
        ]
        chars = [len(t) for t in full_translations[label]]
        metrics[label] = {
            "repeats": REPEATS,
            "segments_per_repeat": len(repeats[0]) if repeats else 0,
            "mean_output_chars": _mean(chars),
            "mean_ttft_seconds": _mean(ttfts),
            "retention_scores": judge_scores[label],
            "mean_retention_score": _mean(judge_scores[label]),
            "within_condition_retention_stdev": _stdev(judge_scores[label]),
        }

    baseline_mean = metrics["baseline_replay"]["mean_retention_score"]
    alt_mean = metrics["alternating_turns"]["mean_retention_score"]
    delta = (
        alt_mean - baseline_mean
        if baseline_mean is not None and alt_mean is not None
        else None
    )
    noise_floor = max(
        metrics["baseline_replay"]["within_condition_retention_stdev"] or 0.0,
        metrics["alternating_turns"]["within_condition_retention_stdev"] or 0.0,
    )

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "alternating_turn_context_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "note": (
                "Not a live pipeline run -- Deepgram listen-websocket "
                "confirmed blocked this session (HTTP 400 'Connection "
                "header did not include upgrade', same signature as every "
                "prior cycle, re-tested this cycle with a freshly-built "
                "python3.13 venv). Replays the same already-recorded, "
                "matched (asr, translation) segment pairs through "
                "LLMTranslator.translate_stream() directly, sequentially/"
                "single-threaded. Only the prompt's context/history "
                "formatting (history_pairs vs context_lines+"
                "previous_translations) differs between conditions; the "
                "prior-window content and size (3 segments) are identical."
            ),
        },
        "models": src_models,
        "config": {
            "context_window": CONTEXT_WINDOW,
            "repeats": REPEATS,
        },
        "results": {
            label: {
                "full_translations_per_repeat": full_translations[label],
                "segments_per_repeat": [
                    [asdict(r) for r in results] for results in repeats
                ],
            }
            for label, repeats in all_repeats.items()
        },
        "metrics": {
            **metrics,
            "mean_retention_delta_alt_minus_baseline": delta,
            "within_condition_noise_floor_stdev": noise_floor,
            "delta_within_noise_floor": (
                abs(delta) <= noise_floor
                if delta is not None and noise_floor
                else None
            ),
        },
        "notes": (
            "h-alternating-turn-context-format. Grounded in "
            "huang2024-conversational-simulmt. REPEATS=2 full independent "
            "passes per condition so a same-condition-twice noise check "
            "(reflections.md cycle 19-21's method) can be done before "
            "trusting mean_retention_delta_alt_minus_baseline as a real "
            "effect rather than resampling noise."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {OUT_JSON}")
    print(f"baseline_replay: mean_retention={baseline_mean}")
    print(f"alternating_turns: mean_retention={alt_mean}")
    print(f"delta={delta}, within_condition_noise_floor_stdev={noise_floor}")


if __name__ == "__main__":
    asyncio.run(main())

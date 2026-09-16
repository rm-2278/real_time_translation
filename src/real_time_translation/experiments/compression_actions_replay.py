"""h-compression-actions-prompt-instruction: does an explicit compression
instruction (paper-inspired SENTENCE_CUT/DROP/PARTIAL_SUMMARIZATION/
PRONOMINALIZATION) beat verbatim translation on length/latency without the
fidelity cost seen in Config.reading_speed_budget_translation (see commits
33faed4/c4fd798 and experiments/20260915_budget_translation_*.json)?

Deepgram's listen-websocket was confirmed blocked again this cycle (same
proxy WS-upgrade-mangling issue as prior cycles, see PLAYBOOK.md), so this
does not run the live pipeline. Instead it replays the already-recorded
ASR segments from experiments/20260915_budget_translation_baseline.json
(reading_speed_budget_translation=False there, i.e. verbatim baseline)
through the real LLMTranslator, sequentially and single-threaded (same
pattern as compare_translation_models.py), twice: once with
extra_system_instructions=None (a fresh "baseline" control, NOT assumed
identical to the original recorded baseline -- only used as this script's
own paired control) and once with the new compression instruction. Both
conditions get the same model/dictionary/context settings, so
extra_system_instructions is the only variable under test. Reuses
translation_fidelity_judge.judge() (added in c4fd798) to score information
retention for each condition against the same English source.

Usage:
  python3 -m real_time_translation.experiments.compression_actions_replay
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENT = Path("experiments/20260915_budget_translation_baseline.json")
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20260916_compression_actions_prompt.json")

# Deliberately conservative and bounded (mirrors rule 8's own "never drop
# substantive content" guardrail) -- see h-compression-actions-prompt-
# instruction in research_agent/state/hypotheses.json for the full
# rationale and the paper it's grounded in.
COMPRESSION_INSTRUCTIONS = (
    "9. COMPRESS LONG OR REDUNDANT CONTENT (EXPERIMENTAL): When <target> is\n"
    "unusually long, packs in multiple loosely-connected clauses, or\n"
    "restates something already fully said in <context>, you may shorten\n"
    "your Japanese output using these techniques, in order of preference:\n"
    "   a. PRONOMINALIZATION: refer back to an entity already named in\n"
    "      <context> with a pronoun or elision rather than repeating its\n"
    "      full name.\n"
    "   b. SENTENCE_CUT: split an overly long <target> into two shorter,\n"
    "      natural Japanese clauses instead of one convoluted one, if this\n"
    "      does not lose meaning.\n"
    "   c. PARTIAL_SUMMARIZATION: condense a clause that is clearly\n"
    "      redundant or filler-heavy into a shorter paraphrase that\n"
    "      preserves its meaning.\n"
    "   d. DROP: omit a clause ONLY if it is pure repetition of something\n"
    "      already fully conveyed earlier in <context>, never for new\n"
    "      information.\n"
    "   NEVER use these to compress away technical terms, numbers, named\n"
    "   entities, or any claim/fact that appears only once in <target> --\n"
    "   when in doubt, translate it in full rather than risk losing it.\n"
    "   This is more aggressive than rule 8's disfluency smoothing: it\n"
    "   targets structurally long or redundant complete sentences, not\n"
    "   just filler words and stutters."
)


@dataclass
class SegmentResult:
    index: int
    asr_start_time: float
    source_text: str
    translation: str
    ttft_seconds: float | None
    total_seconds: float
    error: str | None = None


async def replay_condition(
    *,
    label: str,
    segments: list[dict],
    api_key: str,
    model: str,
    thinking_budget: int | None,
    extra_system_instructions: str | None,
) -> list[SegmentResult]:
    translator = LLMTranslator(
        provider="gemini",
        api_key=api_key,
        model=model,
        dictionary_path=DICTIONARY_PATH,
        thinking_budget=thinking_budget,
        extra_system_instructions=extra_system_instructions,
    )
    await translator.prepare()

    results: list[SegmentResult] = []
    for i, seg in enumerate(segments):
        text = seg["asr"].strip()
        if not text:
            continue
        t0 = time.time()
        ttft: float | None = None
        full = ""
        error: str | None = None
        try:
            async for chunk in translator.translate_stream(text):
                if ttft is None:
                    ttft = time.time() - t0
                full += chunk
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        results.append(
            SegmentResult(
                index=i,
                asr_start_time=seg["asr_start_time"],
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
    src_config = source_data["config"]
    src_models = source_data["models"]
    full_asr = source_data["results"]["full_asr"]
    assert src_config["reading_speed_budget_translation"] is False, (
        "source experiment must be the verbatim (non-budget) baseline"
    )

    print(f"Replaying {len(segments)} segments x 2 conditions...")
    conditions = {
        "baseline_replay": None,
        "compression_actions": COMPRESSION_INSTRUCTIONS,
    }
    all_results: dict[str, list[SegmentResult]] = {}
    for label, extra in conditions.items():
        print(f"=== {label} ===")
        all_results[label] = await replay_condition(
            label=label,
            segments=segments,
            api_key=config.google_api_key,
            model=src_models["llm_model"],
            thinking_budget=src_models["gemini_thinking_budget"],
            extra_system_instructions=extra,
        )

    full_translations = {
        label: "".join(r.translation for r in results if not r.error)
        for label, results in all_results.items()
    }

    print("Running fidelity judge (retention score vs English source)...")
    judge_results = {}
    for label, translation in full_translations.items():
        judge_results[label] = judge(full_asr, translation)
        print(f"  [{label}] retention_score={judge_results[label].get('score')}")

    metrics = {}
    for label, results in all_results.items():
        ok = [r for r in results if not r.error]
        ttfts = [r.ttft_seconds for r in ok if r.ttft_seconds is not None]
        metrics[label] = {
            "segments": len(results),
            "errors": len([r for r in results if r.error]),
            "output_chars": len(full_translations[label]),
            "avg_ttft_seconds": sum(ttfts) / len(ttfts) if ttfts else None,
            "retention_score": judge_results[label].get("score"),
            "dropped_or_distorted": judge_results[label].get("dropped_or_distorted"),
            "judge_notes": judge_results[label].get("notes"),
        }

    baseline_chars = metrics["baseline_replay"]["output_chars"]
    treatment_chars = metrics["compression_actions"]["output_chars"]
    reduction_pct = (
        100.0 * (baseline_chars - treatment_chars) / baseline_chars
        if baseline_chars
        else None
    )

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "compression_actions_prompt_replay",
        "domain": None,
        "input": {
            "type": "replay",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "note": (
                "Not a live pipeline run -- Deepgram listen-websocket confirmed "
                "blocked this session (see PLAYBOOK.md RUN_EXPERIMENTS notes). "
                "Replays the same already-recorded ASR segments through "
                "LLMTranslator directly, sequentially/single-threaded, "
                "extra_system_instructions is the only variable between the "
                "two conditions."
            ),
        },
        "models": src_models,
        "config": {
            **{
                k: v
                for k, v in src_config.items()
                if k
                not in (
                    "translation_workers",
                    "translation_queue_size",
                    "deepgram_endpointing",
                    "deepgram_utterance_end_ms",
                    "deepgram_max_interim_duration",
                )
            },
            "extra_system_instructions": {
                "baseline_replay": None,
                "compression_actions": "see COMPRESSION_INSTRUCTIONS in "
                "compression_actions_replay.py",
            },
        },
        "results": {
            label: {
                "full_translation": full_translations[label],
                "segments": [asdict(r) for r in results],
            }
            for label, results in all_results.items()
        },
        "metrics": {
            **metrics,
            "output_char_reduction_pct": reduction_pct,
        },
        "notes": (
            "h-compression-actions-prompt-instruction. Grounded in "
            "incremental2026-human-like-strategies. Compares against "
            "reading_speed_budget_translation's already-measured fidelity "
            "cost (commit c4fd798: retention 85 baseline / 60 budget=6.0 / "
            "75 budget=3.0 on a different clip/run -- not directly "
            "comparable numbers, different code path, cited for context only)."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base_retention = metrics["baseline_replay"]["retention_score"]
    treat_retention = metrics["compression_actions"]["retention_score"]
    print(f"\nWrote {OUT_JSON}")
    print(f"baseline_replay: chars={baseline_chars} retention={base_retention}")
    print(f"compression_actions: chars={treatment_chars} retention={treat_retention}")
    if reduction_pct is not None:
        print(f"char reduction: {reduction_pct:.1f}%")
    else:
        print("char reduction: n/a")


if __name__ == "__main__":
    asyncio.run(main())

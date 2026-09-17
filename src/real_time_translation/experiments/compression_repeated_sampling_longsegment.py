"""h-compression-nonduplicate-longsegment: does the compression instruction
still drop genuinely new content on a long, complex segment that is NOT a
duplicate of anything in its own context window?

h-compression-repeated-sampling-segment5 (cycle 15) found a 40% truncation
rate for the compression_actions condition on results.segments[5] of
experiments/20260915_budget_translation_baseline.json, but also discovered
that segment 5's source text is an exact duplicate of segment 4 (which sits
inside segment 5's own <context>) -- so that truncation may be a *correct*
application of the instruction's own rule 9d DROP-on-repetition action, not
a guardrail violation. This script repeats the same design on a different
segment that is long (202 chars), multi-clause, and information-dense, but
verified NOT a duplicate of its own context or its immediate successor:
results.segments[15] of experiments/20260903_asr_keyterms_off.json. Any
material content drop here cannot be justified by rule 9d's repetition
exception, since there is nothing repetitive in this segment or its context
for the DROP action to legitimately target.

Same isolation pattern as compression_repeated_sampling.py: each repeat
passes an explicit, fixed context_lines and update_context=False, so all 10
repeats per condition see exactly the same <context> and don't leak into
each other via the translator's internal context buffer.

Usage:
  python3 -m real_time_translation.experiments.compression_repeated_sampling_longsegment
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from real_time_translation.experiments.compression_actions_replay import (
    COMPRESSION_INSTRUCTIONS,
)
from real_time_translation.translation.llm_translator import LLMTranslator

SOURCE_EXPERIMENT = Path("experiments/20260903_asr_keyterms_off.json")
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path(
    "experiments/20260917_compression_repeated_sampling_longsegment.json"
)

TARGET_SEGMENT_INDEX = 15
CONTEXT_SEGMENT_INDICES = (12, 13, 14)
SUCCESSOR_SEGMENT_INDEX = 16
N_REPEATS = 10
# Spot-check only this many of the N_REPEATS outputs per condition with the
# (paid, LLM-call) fidelity judge, to bound cost -- the cheap heuristic
# below runs on all of them.
N_JUDGE_SAMPLES = 3

# The target segment has two clauses: (A) "which describes any model where
# you're using data to somehow to somehow determine how a model behaves."
# and (B) "What I mean by that is let's say you want a function that takes
# in an image and it produces a label". A translation missing clause B
# entirely is the failure mode this script is designed to catch (mirrors
# clause A always surviving first in a left-to-right generation, so clause
# B dropping is the more informative signal, same asymmetry as segment 5's
# own "course" vs "already know what's" split).
CLAUSE_B_MARKERS = ("関数", "画像", "ラベル")


@dataclass
class SampleResult:
    repeat_index: int
    translation: str
    output_chars: int
    covers_clause_b_heuristic: bool
    error: str | None = None


def _covers_clause_b(translation: str) -> bool:
    """Cheap heuristic: does the output mention any of the concrete nouns
    unique to the second clause (function/image/label -> 関数/画像/ラベル)?
    Unlike segment 5's length-only threshold, this segment's two clauses
    differ enough in concrete content that a keyword check is more direct
    than a char-count cutoff, and is robust to SENTENCE_CUT/PARTIAL_
    SUMMARIZATION rewording clause A's length without touching clause B.
    """
    return any(marker in translation for marker in CLAUSE_B_MARKERS)


async def sample_condition(
    *,
    label: str,
    target_text: str,
    context_lines: list[str],
    api_key: str,
    model: str,
    thinking_budget: int | None,
    extra_system_instructions: str | None,
) -> list[SampleResult]:
    translator = LLMTranslator(
        provider="gemini",
        api_key=api_key,
        model=model,
        dictionary_path=DICTIONARY_PATH,
        thinking_budget=thinking_budget,
        extra_system_instructions=extra_system_instructions,
    )
    await translator.prepare()

    results: list[SampleResult] = []
    for i in range(N_REPEATS):
        error: str | None = None
        full = ""
        try:
            async for chunk in translator.translate_stream(
                target_text,
                context_lines=context_lines,
                update_context=False,
            ):
                full += chunk
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        full = full.strip()
        results.append(
            SampleResult(
                repeat_index=i,
                translation=full,
                output_chars=len(full),
                covers_clause_b_heuristic=_covers_clause_b(full),
                error=error,
            )
        )
        print(f"  [{label}] repeat {i}: {len(full)} chars, ok={not error}")
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

    target_text = segments[TARGET_SEGMENT_INDEX]["asr"].strip()
    context_lines = [segments[i]["asr"].strip() for i in CONTEXT_SEGMENT_INDICES]
    successor_text = segments[SUCCESSOR_SEGMENT_INDEX]["asr"].strip()

    # Precondition this hypothesis depends on: unlike segment 5's source,
    # this target must NOT duplicate any of its context lines or its
    # immediate successor -- otherwise a DROP here could again be excused
    # by rule 9d's repetition exception, defeating the point of this
    # follow-up. Fail loudly rather than silently proceeding if this ever
    # stops holding (e.g. someone points SOURCE_EXPERIMENT elsewhere).
    duplicate_of_context = any(line == target_text for line in context_lines)
    duplicate_of_successor = successor_text == target_text
    assert not duplicate_of_context and not duplicate_of_successor, (
        "target segment duplicates its context or successor -- this "
        "script requires a genuinely non-duplicated segment, see "
        "h-compression-repeated-sampling-segment5's reframing for why"
    )

    print(f"Target segment [{TARGET_SEGMENT_INDEX}]: {target_text!r}")
    print(f"Context lines: {context_lines!r}")
    print("Confirmed: target segment is NOT a duplicate of its context or successor.")

    conditions = {
        "baseline_replay": None,
        "compression_actions": COMPRESSION_INSTRUCTIONS,
    }
    all_results: dict[str, list[SampleResult]] = {}
    for label, extra in conditions.items():
        print(f"=== {label} ({N_REPEATS} repeats) ===")
        all_results[label] = await sample_condition(
            label=label,
            target_text=target_text,
            context_lines=context_lines,
            api_key=config.google_api_key,
            model=src_models["llm_model"],
            thinking_budget=src_models["gemini_thinking_budget"],
            extra_system_instructions=extra,
        )

    print(
        "Running fidelity judge spot-check on first "
        f"{N_JUDGE_SAMPLES} samples per condition (judged against the FULL "
        "segment source text, not a fragment -- unlike segment 5's own "
        "source, this segment is a complete two-clause utterance, so the "
        "judge should be more informative here)..."
    )
    judge_results: dict[str, list[dict]] = {}
    for label, results in all_results.items():
        judge_results[label] = []
        for r in results[:N_JUDGE_SAMPLES]:
            if r.error:
                judge_results[label].append({"score": None, "notes": "call errored"})
                continue
            j = judge(target_text, r.translation)
            judge_results[label].append(j)
            print(f"  [{label}] repeat {r.repeat_index}: score={j.get('score')}")

    summary = {}
    for label, results in all_results.items():
        ok = [r for r in results if not r.error]
        n_covers = sum(1 for r in ok if r.covers_clause_b_heuristic)
        chars = [r.output_chars for r in ok]
        judge_scores = [
            j.get("score") for j in judge_results[label] if j.get("score") is not None
        ]
        summary[label] = {
            "n_repeats": len(results),
            "n_errors": len(results) - len(ok),
            "n_covers_clause_b_heuristic": n_covers,
            "clause_b_drop_rate_heuristic": (
                1.0 - n_covers / len(ok) if ok else None
            ),
            "output_chars_min": min(chars) if chars else None,
            "output_chars_max": max(chars) if chars else None,
            "output_chars_mean": sum(chars) / len(chars) if chars else None,
            "judge_scores_sampled": judge_scores,
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "compression_repeated_sampling_longsegment",
        "domain": "ml_transformers",
        "input": {
            "type": "replay_repeated_single_segment",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "target_segment_index": TARGET_SEGMENT_INDEX,
            "target_segment_text": target_text,
            "context_segment_indices": list(CONTEXT_SEGMENT_INDICES),
            "context_lines": context_lines,
            "successor_segment_index": SUCCESSOR_SEGMENT_INDEX,
            "successor_segment_text": successor_text,
            "target_segment_duplicates_context_or_successor": False,
            "note": (
                "Follow-up to h-compression-repeated-sampling-segment5's "
                "reframing (that hypothesis's target segment duplicated its "
                "own context, so its 40% truncation rate may reflect a "
                "correct rule-9d DROP-on-repetition, not a guardrail "
                "violation). This script targets a different, verified "
                "non-duplicated long/complex segment to isolate whether "
                "genuinely new content still gets dropped."
            ),
        },
        "models": src_models,
        "config": {
            "extra_system_instructions": {
                "baseline_replay": None,
                "compression_actions": "see COMPRESSION_INSTRUCTIONS in "
                "compression_actions_replay.py",
            },
            "n_repeats": N_REPEATS,
            "n_judge_samples": N_JUDGE_SAMPLES,
        },
        "results": {
            label: {"samples": [asdict(r) for r in results]}
            for label, results in all_results.items()
        },
        "judge_results": judge_results,
        "metrics": summary,
        "notes": (
            "h-compression-nonduplicate-longsegment. Grounded in "
            "incremental2026-human-like-strategies. Direct follow-up to "
            "h-compression-repeated-sampling-segment5's reframing "
            "(experiments/20260916_compression_repeated_sampling_segment5.json)."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {OUT_JSON}")
    for label, s in summary.items():
        print(
            f"{label}: clause_b_drop_rate_heuristic="
            f"{s['clause_b_drop_rate_heuristic']} "
            f"(covers_clause_b={s['n_covers_clause_b_heuristic']}/"
            f"{s['n_repeats']}), chars mean={s['output_chars_mean']:.1f} "
            f"min={s['output_chars_min']} max={s['output_chars_max']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

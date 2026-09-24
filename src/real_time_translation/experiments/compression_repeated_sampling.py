"""h-compression-repeated-sampling-segment5: how reliable was the single
guardrail-violating content drop h-compression-actions-prompt-instruction
found on segment 5?

That hypothesis's n=1 replay (compression_actions_replay.py) found that the
compression_actions condition truncated results.segments[5] ("course. So I
think you already know what's") mid-clause, while the paired
baseline_replay condition (extra_system_instructions=None) translated it in
full. n=1 cannot distinguish a reliably reproduced model behavior from a
one-off sampling fluke, so this script repeats the same single-segment
translation N times per condition (default 10) and reports how often each
condition produces a truncated/incomplete output.

Note found while designing this script (not in the original write-up):
results.segments[4] and results.segments[5] in the source experiment JSON
have IDENTICAL source text ("course. So I think you already know what's" --
almost certainly a duplicate/retry artifact of the Deepgram interim stream
that got recorded as two separate committed segments). Segment 4 is part of
segment 5's own <context> window (context_window_size=3 -> segments 2,3,4).
That means a compression_actions translation of segment 5 that omits
content already said in segment 4 is arguably a *correct* application of
the instruction's own DROP action ("omit a clause ONLY if it is pure
repetition of something already fully conveyed earlier in <context>"), not
a violation of it -- the original "guardrail violation" framing may not
hold up once this is accounted for. This script does not resolve that
question on its own (it repeats the same context every time, so it cannot
tell us what happens on a *non*-duplicated segment) -- it only measures how
often truncation happens under this exact, duplicate-context input. Flagging
this explicitly in the output/notes rather than silently keeping the
original "violation" framing.

To isolate run-to-run sampling variance from any context-accumulation
effect (repeatedly calling translate_stream on one shared instance would
have each repeat's context include earlier repeats' identical source text,
which is not how any real single utterance is ever translated), each
repeat passes an explicit, fixed context_lines=[seg2, seg3, seg4] and
update_context=False -- i.e. every repeat sees exactly the same <context>
segment 5 saw in the original replay, no more and no less.

Usage:
  python3 -m real_time_translation.experiments.compression_repeated_sampling
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

SOURCE_EXPERIMENT = Path("experiments/20260915_budget_translation_baseline.json")
DICTIONARY_PATH = Path("dictionary.csv")
OUT_JSON = Path("experiments/20260916_compression_repeated_sampling_segment5.json")

TARGET_SEGMENT_INDEX = 5
CONTEXT_SEGMENT_INDICES = (2, 3, 4)
N_REPEATS = 10
# Spot-check only this many of the N_REPEATS outputs per condition with the
# (paid, LLM-call) fidelity judge, to bound cost -- the cheap heuristic
# below runs on all of them.
N_JUDGE_SAMPLES = 3


@dataclass
class SampleResult:
    repeat_index: int
    translation: str
    output_chars: int
    covers_both_clauses_heuristic: bool
    error: str | None = None


def _covers_both_clauses(translation: str) -> bool:
    """Cheap heuristic: does the output look like it covers both halves of
    the two-clause source ("course[...]" and "[...]already know what's")
    rather than stopping after only one? Real Japanese wording varies run
    to run, so this checks output length against the two single-condition
    replay outputs observed in the original run, not exact substrings.
    A translation this short is very unlikely to render both clauses.
    Threshold (20 chars) is calibrated against the two outputs the original
    n=1 run actually produced for this exact segment: baseline_replay was
    34 chars (both clauses present), compression_actions was 12 chars
    (truncated after the first clause) -- 20 sits with margin on both sides.
    """
    return len(translation.strip()) >= 20


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
                covers_both_clauses_heuristic=_covers_both_clauses(full),
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
    duplicate_context_note = (
        segments[CONTEXT_SEGMENT_INDICES[-1]]["asr"].strip() == target_text
    )

    print(f"Target segment [{TARGET_SEGMENT_INDEX}]: {target_text!r}")
    print(f"Context lines: {context_lines!r}")
    if duplicate_context_note:
        print(
            "NOTE: target segment text is IDENTICAL to the immediately "
            "preceding context line -- see module docstring."
        )

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

    print("Running fidelity judge spot-check on first "
          f"{N_JUDGE_SAMPLES} samples per condition...")
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
        n_covers = sum(1 for r in ok if r.covers_both_clauses_heuristic)
        chars = [r.output_chars for r in ok]
        judge_scores = [
            j.get("score") for j in judge_results[label] if j.get("score") is not None
        ]
        summary[label] = {
            "n_repeats": len(results),
            "n_errors": len(results) - len(ok),
            "n_covers_both_clauses_heuristic": n_covers,
            "truncation_rate_heuristic": (
                1.0 - n_covers / len(ok) if ok else None
            ),
            "output_chars_min": min(chars) if chars else None,
            "output_chars_max": max(chars) if chars else None,
            "output_chars_mean": sum(chars) / len(chars) if chars else None,
            "judge_scores_sampled": judge_scores,
        }

    out = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "experiment_name": "compression_repeated_sampling_segment5",
        "domain": None,
        "input": {
            "type": "replay_repeated_single_segment",
            "source_experiment": str(SOURCE_EXPERIMENT),
            "target_segment_index": TARGET_SEGMENT_INDEX,
            "target_segment_text": target_text,
            "context_segment_indices": list(CONTEXT_SEGMENT_INDICES),
            "context_lines": context_lines,
            "target_segment_duplicates_context_tail": duplicate_context_note,
            "note": (
                "Follow-up to h-compression-actions-prompt-instruction's n=1 "
                "finding. Each repeat uses an explicit, fixed context_lines "
                "(update_context=False) so repeats don't accumulate state "
                "into each other's context -- every repeat sees exactly the "
                "context the original segment-5 translation saw."
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
            "h-compression-repeated-sampling-segment5. Grounded in "
            "incremental2026-human-like-strategies. Direct follow-up to "
            "h-compression-actions-prompt-instruction's segment-5 n=1 "
            "finding (experiments/20260916_compression_actions_prompt.json)."
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {OUT_JSON}")
    for label, s in summary.items():
        print(
            f"{label}: truncation_rate_heuristic="
            f"{s['truncation_rate_heuristic']} "
            f"(covers_both={s['n_covers_both_clauses_heuristic']}/"
            f"{s['n_repeats']}), chars mean={s['output_chars_mean']:.1f} "
            f"min={s['output_chars_min']} max={s['output_chars_max']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

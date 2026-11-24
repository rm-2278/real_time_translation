"""h-judge-pairwise-anchored-comparison-sensitivity: does a pairwise
comparison judge format (shown both candidates at once, asked which better
retains source content) detect this repo's own known modest fidelity
differences more consistently than two independent absolute judge() calls?

jptlbench2026-anchored-pairwise-ja-en and rate2026-beyond-literal-mapping-
nontransliteval's Comparison Agent both converge on pairwise comparison as
a judge-stabilization technique. Every existing judge-noise hypothesis in
this backlog (judge_reasoning_order_replay.py, judge_block_order_replay.py,
judge_holistic_vs_decomposed_noise.py, judge_repeat_diminishing_returns.py)
varies the ISOLATED absolute-scoring format itself; none has tried a
pairwise format.

Test bed: experiments/20260916_compression_actions_prompt.json's
baseline_replay (single-call judge() retention_score=75, logged in
results.csv 2026-09-16) vs compression_actions (retention_score=85) --
a real, already-recorded 10-point difference on the identical source text,
deliberately modest (not dramatic) so this stays in the noisy regime
cycle 19-20's reflections.md flagged for the isolated-scoring format.

Design: N_REPEATS new pairwise judge calls, half with compression_actions
in slot A / baseline_replay in slot B and half with the assignment
swapped, so a directional "pick A" bias can be told apart from a genuine
content preference (same spirit as judge_block_order_replay.py's slot
swap). Zero new Deepgram spend, zero new translation generation -- only
new spend is N_REPEATS new gemini-3.1-flash-lite judge calls on already-
recorded text.

Usage:
  python3 -m real_time_translation.experiments.judge_pairwise_comparison
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

SOURCE_JSON = Path("experiments/20260916_compression_actions_prompt.json")
OUT_JSON = Path("experiments/judge_pairwise_comparison.json")

# Candidate better per the already-recorded single-call retention scores
# (metrics.compression_actions.retention_score=85 > metrics.baseline_replay
# .retention_score=75, both logged in results.csv 2026-09-16).
BETTER_CONDITION = "compression_actions"
WORSE_CONDITION = "baseline_replay"
N_REPEATS = 20  # 10 with BETTER as slot A, 10 with BETTER as slot B

_PAIRWISE_PROMPT = """You are a strict bilingual (English/Japanese) \
fact-checker comparing two real-time speech translations of the SAME \
English source for information retention, not fluency. You will be shown \
the English source transcript (may contain ASR disfluencies -- ignore \
those) and two Japanese translations, labeled Translation A and \
Translation B.

Decide which translation retains more of the source's substantive \
content (facts, numbers, technical terms, named entities, causal/logical \
relationships), independent of how natural the Japanese reads. If they \
are genuinely equivalent in what they retain, say so.

Respond with ONLY a JSON object, no markdown fences:
{{"verdict": "A" | "B" | "tie", "reasoning": "one or two sentences"}}

<source_english>
{source}
</source_english>

<translation_a>
{translation_a}
</translation_a>

<translation_b>
{translation_b}
</translation_b>
"""


def _judge_pairwise_once(source: str, translation_a: str, translation_b: str) -> dict:
    from dotenv import load_dotenv
    from google import genai

    load_dotenv(override=True)
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    prompt = _PAIRWISE_PROMPT.format(
        source=source, translation_a=translation_a, translation_b=translation_b
    )
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
    )
    text = response.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Judge did not return JSON: {text[:300]!r}")
    return json.loads(match.group(0))


def _reconstruct_source(segments: list[dict]) -> str:
    """Joins per-segment source_text in order -- the same already-recorded
    ASR text both conditions in this file were translated from (this
    source JSON has no top-level full_asr field, unlike some other
    experiment JSONs in this repo)."""
    return " ".join(seg["source_text"] for seg in segments)


def run(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    results = data["results"]
    better_translation = results[BETTER_CONDITION]["full_translation"]
    worse_translation = results[WORSE_CONDITION]["full_translation"]
    source_full = _reconstruct_source(results[BETTER_CONDITION]["segments"])

    rows: list[dict] = []
    for i in range(N_REPEATS):
        better_slot = "A" if i % 2 == 0 else "B"
        if better_slot == "A":
            translation_a, translation_b = better_translation, worse_translation
        else:
            translation_a, translation_b = worse_translation, better_translation

        result = _judge_pairwise_once(source_full, translation_a, translation_b)
        verdict_slot = result.get("verdict")
        if verdict_slot == better_slot:
            content_preferred = BETTER_CONDITION
        elif verdict_slot in ("A", "B"):
            content_preferred = WORSE_CONDITION
        else:
            content_preferred = "tie"

        rows.append(
            {
                "repeat_index": i,
                "better_slot": better_slot,
                "verdict_slot": verdict_slot,
                "content_preferred": content_preferred,
                "reasoning": result.get("reasoning"),
            }
        )

    n = len(rows)
    better_wins = sum(1 for r in rows if r["content_preferred"] == BETTER_CONDITION)
    worse_wins = sum(1 for r in rows if r["content_preferred"] == WORSE_CONDITION)
    ties = sum(1 for r in rows if r["content_preferred"] == "tie")

    a_slot_rows = [r for r in rows if r["better_slot"] == "A"]
    b_slot_rows = [r for r in rows if r["better_slot"] == "B"]
    a_slot_better_wins = sum(
        1 for r in a_slot_rows if r["content_preferred"] == BETTER_CONDITION
    )
    b_slot_better_wins = sum(
        1 for r in b_slot_rows if r["content_preferred"] == BETTER_CONDITION
    )
    verdict_a_rate = sum(1 for r in rows if r["verdict_slot"] == "A") / n if n else None

    return {
        "source": str(source_path),
        "better_condition": BETTER_CONDITION,
        "worse_condition": WORSE_CONDITION,
        "known_retention_scores": {
            BETTER_CONDITION: 85,
            WORSE_CONDITION: 75,
        },
        "n_repeats": n,
        "content_preference": {
            "better_condition_win_rate": better_wins / n if n else None,
            "worse_condition_win_rate": worse_wins / n if n else None,
            "tie_rate": ties / n if n else None,
            "better_wins": better_wins,
            "worse_wins": worse_wins,
            "ties": ties,
        },
        "position_bias_check": {
            "verdict_slot_a_rate": verdict_a_rate,
            "better_condition_win_rate_when_in_slot_a": (
                a_slot_better_wins / len(a_slot_rows) if a_slot_rows else None
            ),
            "better_condition_win_rate_when_in_slot_b": (
                b_slot_better_wins / len(b_slot_rows) if b_slot_rows else None
            ),
            "notes": (
                "If verdict_slot_a_rate is far from 0.5 regardless of which "
                "condition occupies slot A, that is a raw A/B positional bias "
                "in the prompt/model itself. If better_condition_win_rate "
                "differs sharply between slot A and slot B, swapping sides "
                "is changing the content verdict, not just a label -- a "
                "stronger warning sign than the raw slot-A rate alone."
            ),
        },
        "rows": rows,
        "notes": (
            "Pairwise-comparison judge format (no numeric score, single "
            "verdict: A/B/tie) applied to an already-established, modest "
            "(10-point) real retention-score difference between two "
            "already-recorded full_translation texts on the identical "
            "source. Zero new Deepgram spend, zero new translation "
            "generation -- only new spend is N_REPEATS new gemini-3.1-"
            "flash-lite judge calls. Single pair, not a well-powered sweep "
            "across many pairs -- a clear result here is suggestive, not "
            "conclusive proof for every magnitude of difference, same "
            "discipline as this backlog's other single-pair judge-noise "
            "findings."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = run(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

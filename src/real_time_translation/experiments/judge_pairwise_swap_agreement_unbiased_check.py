"""h-judge-pairwise-swap-agreement-unbiased-check: does the swap-and-
require-agreement pairwise-judge design (soumik2026-judging-the-judges-
debiasing-factorial / xu2026-pointwise-pairwise-rubric-position-bias)
correctly show HIGH agreement -- not a false negative -- on a pair with a
real, obvious (not subtle) quality gap?

Complements cycle 101's h-judge-pairwise-swap-agreement-mitigation-
retroactive, which confirmed the mitigation correctly discards every pair
(agreement_rate=0.0) on a comparison that was ALREADY known to be 100%
position-driven (the subtle 10-point baseline_replay/compression_actions
pair). That cycle's reflections.md explicitly flagged the untested
complementary case: does the same mitigation correctly show HIGH
agreement when a comparison is genuinely unbiased and the content signal
is strong?

Test bed: experiments/20260919_h_soft_anchor_generalization_guest_talk.json
span_index=9 (source_full='subaward by subaward, and we will have and we
will have a embedded'), where the existing single-call judge() already
scored baseline_replay=60 (dropped_or_distorted=['we will have']) vs
soft_anchor_replay=100 (nothing dropped) -- a 40-point gap with a
concrete, checkable cause.

IMPORTANT (verified by reading soft_anchor_generalization_replay.py
before writing this, not assumed): that script's judge_scores are NOT
computed against each condition's full joined translation -- line 99
reads `translation_full = repeats[0][-1] if repeats[0] else ""`, i.e.
only the LAST batch's translation text of repeat 0, compared against the
span's full source_full. To make this a faithful, apples-to-apples
complement to the already-recorded 60-vs-100 scores (not a different,
unrelated comparison), this script uses the exact same texts: each
condition's condition_repeats[condition][0][-1] (repeat 0, last batch),
not a full/joined translation. This differs from this hypothesis's own
`required_changes` text (which assumed repeat index 1, joined across
batches) -- noted here and in the hypothesis's result_summary as a
correction made after reading the actual generating code, the same
"verify empirically" discipline PLAYBOOK.md's GENERATE_HYPOTHESES
section has flagged repeatedly for event-field assumptions.

Design: swap-and-require-agreement from the start (not a naive
single-order run later patched with a separate retroactive pass), per
cycle 101's stated lesson. N_REPEATS=20 new gemini-3.1-flash-lite judge
calls (10 with soft_anchor_replay in slot A / baseline_replay in slot B,
10 swapped), paired index-for-index within each slot group using the same
agreement logic as judge_pairwise_swap_agreement_retroactive.py. Zero new
Deepgram spend, zero new translation generation.

Usage:
  python3 -m real_time_translation.experiments.\
judge_pairwise_swap_agreement_unbiased_check
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

SOURCE_JSON = Path("experiments/20260919_h_soft_anchor_generalization_guest_talk.json")
SPAN_INDEX = 9
OUT_JSON = Path(
    f"experiments/{date.today():%Y%m%d}_h_judge_pairwise_swap_agreement_unbiased_check.json"
)

# Candidate better/worse per the already-recorded single-call judge scores
# in the span's own judge_scores (soft_anchor_replay=100, baseline_replay=60,
# a 40-point gap with a concrete, checkable cause -- baseline drops "we will
# have").
BETTER_CONDITION = "soft_anchor_replay"
WORSE_CONDITION = "baseline_replay"
N_REPEATS = 20  # 10 with BETTER as slot A, 10 with BETTER as slot B

# Same prompt as judge_pairwise_comparison.py (not imported, to keep this
# script standalone and avoid any accidental shared-state coupling between
# two independent judge-noise hypotheses).
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


def run(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    span = data["results"]["spans"][SPAN_INDEX]
    assert span["span_index"] == SPAN_INDEX

    source_full = span["source_full"]
    better_translation = span["condition_repeats"][BETTER_CONDITION][0][-1]
    worse_translation = span["condition_repeats"][WORSE_CONDITION][0][-1]
    known_scores = {
        BETTER_CONDITION: span["judge_scores"][BETTER_CONDITION]["score"],
        WORSE_CONDITION: span["judge_scores"][WORSE_CONDITION]["score"],
    }

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

    a_slot_rows = [r for r in rows if r["better_slot"] == "A"]
    b_slot_rows = [r for r in rows if r["better_slot"] == "B"]
    n_pairs = min(len(a_slot_rows), len(b_slot_rows))

    pairs = []
    for i in range(n_pairs):
        row_a = a_slot_rows[i]
        row_b = b_slot_rows[i]
        agree = row_a["content_preferred"] == row_b["content_preferred"]
        pairs.append(
            {
                "pair_index": i,
                "a_slot_row_repeat_index": row_a["repeat_index"],
                "b_slot_row_repeat_index": row_b["repeat_index"],
                "a_slot_row_content_preferred": row_a["content_preferred"],
                "b_slot_row_content_preferred": row_b["content_preferred"],
                "agree": agree,
                "trusted_winner": row_a["content_preferred"] if agree else None,
            }
        )

    n_agree = sum(1 for p in pairs if p["agree"])
    agreement_rate = n_agree / n_pairs if n_pairs else None
    trusted_better_wins = sum(
        1 for p in pairs if p["agree"] and p["trusted_winner"] == BETTER_CONDITION
    )
    trusted_worse_wins = sum(
        1 for p in pairs if p["agree"] and p["trusted_winner"] == WORSE_CONDITION
    )

    n = len(rows)
    naive_better_wins = sum(
        1 for r in rows if r["content_preferred"] == BETTER_CONDITION
    )
    naive_worse_wins = sum(
        1 for r in rows if r["content_preferred"] == WORSE_CONDITION
    )
    naive_ties = sum(1 for r in rows if r["content_preferred"] == "tie")
    verdict_a_rate = sum(1 for r in rows if r["verdict_slot"] == "A") / n if n else None

    return {
        "source": str(source_path),
        "span_index": SPAN_INDEX,
        "source_full": source_full,
        "better_condition": BETTER_CONDITION,
        "worse_condition": WORSE_CONDITION,
        "known_judge_scores": known_scores,
        "n_repeats": n,
        "swap_agreement_mitigation": {
            "method": (
                "Built in from the start (not a later retroactive pass): "
                "10 repeats with BETTER_CONDITION in slot A, 10 with it in "
                "slot B; pair index-for-index across the two slot groups "
                "and trust only agreeing pairs' verdicts, per soumik2026/"
                "xu2026's swap-and-aggregate mitigation."
            ),
            "n_pairs": n_pairs,
            "agreement_rate": agreement_rate,
            "n_agree": n_agree,
            "n_disagree": n_pairs - n_agree,
            "trusted_better_wins": trusted_better_wins,
            "trusted_worse_wins": trusted_worse_wins,
            "pairs": pairs,
        },
        "naive_pooled_aggregate_for_contrast": {
            "better_condition_win_rate": naive_better_wins / n if n else None,
            "worse_condition_win_rate": naive_worse_wins / n if n else None,
            "tie_rate": naive_ties / n if n else None,
        },
        "position_bias_check": {
            "verdict_slot_a_rate": verdict_a_rate,
            "notes": (
                "If verdict_slot_a_rate is far from 0.5 regardless of which "
                "condition occupies slot A, that is a raw positional bias "
                "in the prompt/model itself, independent of the swap-and-"
                "agree pairing result above."
            ),
        },
        "rows": rows,
        "notes": (
            "Swap-and-require-agreement pairwise-judge design applied to a "
            "real, obvious (40-point) already-recorded fidelity gap on "
            "span_index=9 of "
            "experiments/20260919_h_soft_anchor_generalization_guest_talk.json "
            "(baseline_replay drops 'we will have' entirely; soft_anchor_replay "
            "retains it). Texts used are each condition's "
            "condition_repeats[condition][0][-1] (repeat 0, last batch), "
            "matching exactly what soft_anchor_generalization_replay.py's "
            "own judge() call scored 60/100 on -- see this module's "
            "docstring for why (that script judges only the last batch's "
            "translation against the full span source, not a joined full "
            "translation). Zero new Deepgram spend, zero new translation "
            "generation -- only new spend is N_REPEATS new gemini-3.1-"
            "flash-lite judge calls. Single pair, not a sweep -- a clear "
            "result here is suggestive, not conclusive proof for every "
            "magnitude of difference."
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
    summary["swap_agreement_mitigation"] = {
        k: v for k, v in result["swap_agreement_mitigation"].items() if k != "pairs"
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

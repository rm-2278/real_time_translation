"""h-judge-pairwise-swap-agreement-mitigation-retroactive: does the
standard "swap positions, keep only verdicts that agree across both
orders" mitigation (soumik2026-judging-the-judges-debiasing-factorial's
most-common-fix position-swapping; xu2026-pointwise-pairwise-rubric-
position-bias's balanced-permutation-then-aggregate) correctly flag the
already-confirmed deterministic position bias in
judge_pairwise_comparison.py's output as unusable, instead of a naive
pooled aggregate silently reporting a misleading result?

Pure retroactive re-analysis of experiments/judge_pairwise_comparison.json
(h-judge-pairwise-anchored-comparison-sensitivity, cycle 100) -- zero new
judge calls, zero new spend. That file's 20 rows already contain both a
`better_slot` (which slot the ground-truth-better condition, per the
already-recorded retention scores, occupied) and `verdict_slot` (which
slot the judge actually picked) for each repeat. The 10 rows with
better_slot=='A' and the 10 with better_slot=='B' are independent i.i.d.
repeats of the identical (compression_actions vs baseline_replay)
comparison under opposite slot assignments -- exactly the two "swapped"
arms the mitigation technique would compare.

Usage:
  python3 -m real_time_translation.experiments.judge_pairwise_swap_agreement_retroactive
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

SOURCE_JSON = Path("experiments/judge_pairwise_comparison.json")
OUT_JSON = Path(
    f"experiments/{date.today():%Y%m%d}_h_judge_pairwise_swap_agreement_retroactive.json"
)


def run(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    rows = data["rows"]
    better_condition = data["better_condition"]
    worse_condition = data["worse_condition"]

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
        1 for p in pairs if p["agree"] and p["trusted_winner"] == better_condition
    )
    trusted_worse_wins = sum(
        1 for p in pairs if p["agree"] and p["trusted_winner"] == worse_condition
    )

    n_rows = len(rows)
    naive_better_wins = sum(
        1 for r in rows if r["content_preferred"] == better_condition
    )
    naive_worse_wins = sum(
        1 for r in rows if r["content_preferred"] == worse_condition
    )
    naive_ties = sum(
        1
        for r in rows
        if r["content_preferred"] not in (better_condition, worse_condition)
    )

    return {
        "source": str(source_path),
        "better_condition": better_condition,
        "worse_condition": worse_condition,
        "known_retention_scores": data.get("known_retention_scores"),
        "n_pairs": n_pairs,
        "swap_agreement_mitigation": {
            "method": (
                "Pair each better_slot=='A' row with a better_slot=='B' row "
                "(index-order; both groups are independent i.i.d. repeats of "
                "the identical comparison, not a same-instance-twice design, "
                "so any pairing is valid). A pair 'agrees' if both members' "
                "content_preferred match; only agreeing pairs' verdicts are "
                "trusted, per soumik2026/xu2026's swap-and-aggregate mitigation."
            ),
            "agreement_rate": agreement_rate,
            "n_agree": n_agree,
            "n_disagree": n_pairs - n_agree,
            "trusted_better_wins": trusted_better_wins,
            "trusted_worse_wins": trusted_worse_wins,
            "pairs": pairs,
        },
        "naive_pooled_aggregate_for_contrast": {
            "method": (
                "Counts content_preferred across all rows, ignoring slot "
                "entirely -- the aggregate a session would compute if it did "
                "NOT apply the swap-and-agree mitigation."
            ),
            "n_rows": n_rows,
            "better_condition_win_rate": naive_better_wins / n_rows if n_rows else None,
            "worse_condition_win_rate": naive_worse_wins / n_rows if n_rows else None,
            "tie_rate": naive_ties / n_rows if n_rows else None,
        },
        "notes": (
            "Retroactive-only analysis, zero new judge calls, zero new "
            "spend. Tests whether the swap-and-check-agreement mitigation "
            "(soumik2026's named most-common debiasing fix; xu2026's "
            "balanced-permutation-then-aggregate) correctly identifies "
            "cycle 100's already-confirmed deterministic pairwise-judge "
            "position bias as unusable (agreement_rate far below what a "
            "genuinely content-driven verdict would show), in contrast to "
            "a naive pooled aggregate that ignores slot and may report a "
            "misleadingly clean-looking result instead."
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

    summary = {
        k: v
        for k, v in result.items()
        if k not in ("swap_agreement_mitigation",)
    }
    summary["swap_agreement_mitigation"] = {
        k: v
        for k, v in result["swap_agreement_mitigation"].items()
        if k != "pairs"
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

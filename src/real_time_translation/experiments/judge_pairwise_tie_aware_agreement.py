"""h-judge-pairwise-tie-decisive-aware-agreement-metric: does a refined
swap-and-agree metric that treats a "tie" verdict as a soft/excluded
disagreement (not a hard mismatch against a decisive verdict) separate
cycle 101's genuine position-flip case from cycle 102's
tie-vs-decisive-asymmetry case, instead of scoring both as equally low
agreement?

Both cycle 101 (judge_pairwise_swap_agreement_retroactive.py, over
experiments/judge_pairwise_comparison.json) and cycle 102
(judge_pairwise_swap_agreement_unbiased_check.py, over
experiments/20261126_h_judge_pairwise_swap_agreement_unbiased_check.json)
used the identical strict-equality agreement check
(`row_a["content_preferred"] == row_b["content_preferred"]`) and both
reported low agreement_rate (0.0 and 0.2). But cycle 102's own
result_summary showed these are different failure modes: cycle 101 is a
genuine REVERSED verdict (content-blind flip to slot B regardless of
which condition is actually better), while cycle 102 has ZERO reversed
verdicts but 8/10 slot-A repeats hedge with "tie" instead of committing
to the same decisive winner the identical content supported in 10/10
slot-B repeats. A strict-equality check cannot tell these apart -- both
score as `agree=False`.

This script is a pure retroactive re-analysis of the two already-
committed JSON files -- zero new judge calls, zero new spend.

Usage:
  python3 -m real_time_translation.experiments.judge_pairwise_tie_aware_agreement
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

SOURCE_JSONS = [
    Path("experiments/judge_pairwise_comparison.json"),
    Path("experiments/20261126_h_judge_pairwise_swap_agreement_unbiased_check.json"),
]
OUT_JSON = Path(
    f"experiments/{date.today():%Y%m%d}_h_judge_pairwise_tie_aware_agreement.json"
)


def _pair_rows(rows: list[dict]) -> list[tuple[dict, dict]]:
    """Reproduces judge_pairwise_swap_agreement_retroactive.py's exact
    index-for-index better_slot=='A'-vs-better_slot=='B' pairing: both
    groups are independent i.i.d. repeats of the identical comparison
    under opposite slot assignments, so any index pairing is valid."""
    a_slot_rows = [r for r in rows if r["better_slot"] == "A"]
    b_slot_rows = [r for r in rows if r["better_slot"] == "B"]
    n_pairs = min(len(a_slot_rows), len(b_slot_rows))
    return [(a_slot_rows[i], b_slot_rows[i]) for i in range(n_pairs)]


def _classify_pair(row_a: dict, row_b: dict) -> str:
    """Three-way classification using the literal string "tie" as it
    actually appears in content_preferred fields (confirmed present in
    the unbiased_check file's rows, confirmed absent from
    judge_pairwise_comparison.json's rows)."""
    pref_a = row_a["content_preferred"]
    pref_b = row_b["content_preferred"]
    if pref_a == "tie" or pref_b == "tie":
        return "tie_hedge"
    if pref_a == pref_b:
        return "agree"
    return "reversed"


def analyze_file(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    rows = data["rows"]
    better_condition = data["better_condition"]
    worse_condition = data["worse_condition"]

    pairs = []
    for i, (row_a, row_b) in enumerate(_pair_rows(rows)):
        category = _classify_pair(row_a, row_b)
        pairs.append(
            {
                "pair_index": i,
                "a_slot_row_repeat_index": row_a["repeat_index"],
                "b_slot_row_repeat_index": row_b["repeat_index"],
                "a_slot_row_content_preferred": row_a["content_preferred"],
                "b_slot_row_content_preferred": row_b["content_preferred"],
                "category": category,
            }
        )

    n_pairs = len(pairs)
    n_agree = sum(1 for p in pairs if p["category"] == "agree")
    n_reversed = sum(1 for p in pairs if p["category"] == "reversed")
    n_tie_hedge = sum(1 for p in pairs if p["category"] == "tie_hedge")

    strict_agreement_rate = n_agree / n_pairs if n_pairs else None
    # Tie-exclusive effective agreement rate: excludes tie_hedge pairs from
    # the denominator entirely, rather than counting them as disagreement
    # alongside a genuine reversal.
    tie_exclusive_denominator = n_agree + n_reversed
    tie_exclusive_agreement_rate = (
        n_agree / tie_exclusive_denominator if tie_exclusive_denominator else None
    )

    return {
        "source": str(source_path),
        "better_condition": better_condition,
        "worse_condition": worse_condition,
        "n_pairs": n_pairs,
        "category_counts": {
            "agree": n_agree,
            "reversed": n_reversed,
            "tie_hedge": n_tie_hedge,
        },
        "category_rates": {
            "agree": n_agree / n_pairs if n_pairs else None,
            "reversed": n_reversed / n_pairs if n_pairs else None,
            "tie_hedge": n_tie_hedge / n_pairs if n_pairs else None,
        },
        "strict_agreement_rate": strict_agreement_rate,
        "tie_exclusive_agreement_rate": tie_exclusive_agreement_rate,
        "tie_exclusive_denominator": tie_exclusive_denominator,
        "pairs": pairs,
    }


def run() -> dict:
    per_file = [analyze_file(p) for p in SOURCE_JSONS]
    return {
        "sources": [str(p) for p in SOURCE_JSONS],
        "per_file_results": per_file,
        "notes": (
            "Retroactive-only, zero new judge calls, zero new spend. "
            "Reproduces judge_pairwise_swap_agreement_retroactive.py's "
            "pairing method (import-free re-implementation, verified "
            "identical against its source) but replaces the strict "
            "content_preferred-equality agreement check with a three-way "
            "classification (agree / reversed / tie_hedge) and reports a "
            "tie-exclusive effective agreement rate (agree / (agree + "
            "reversed), tie_hedge pairs excluded from that ratio's "
            "denominator but reported separately in full). Prediction: "
            "the first source file (cycle 101, judge_pairwise_comparison.json, "
            "0 ties in its 20 raw rows) should show nearly all its "
            "non-agreeing pairs as 'reversed' and ~0 as 'tie_hedge', while "
            "the second source file (cycle 102's unbiased_check, 8 ties "
            "among its 20 raw rows) should show nearly all its "
            "non-agreeing pairs as 'tie_hedge' and ~0 as 'reversed' -- "
            "validating that the tie-vs-reversal distinction explains the "
            "gap between the two cycles' confusingly similar low strict "
            "agreement_rate numbers (0.0 and 0.2). Two pairs of files, not "
            "a sweep -- this demonstrates the distinction on these two "
            "specific already-known cases, not a general law."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    result = run()

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    summary = {
        "sources": result["sources"],
        "per_file_summary": [
            {k: v for k, v in r.items() if k != "pairs"}
            for r in result["per_file_results"]
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    main()

"""h-judge-schematic-adherence-knowncost-resample: retry the cycle-53
schematic-adherence rubric-decomposition check on a sample with a
DOCUMENTED, pre-existing fidelity cost, instead of cycle 53's ceiling-effect
sample.

Reuses judge_decomposed()/_pairwise_correlations()/_ols_r_squared()/
SUB_CRITERIA from judge_schematic_adherence_replay.py unchanged (same
prompt, same stats, same per-call cost) -- only the point-extraction
differs, because this source file's schema is a fixed-source
repeated-sampling design (experiments/20260916_compression_repeated_sampling_
segment5.json's `results[cond]["samples"][i]["translation"]` against a single
shared `input.target_segment_text`), not the by_source/spans/
condition_repeats schema judge_schematic_adherence_replay.py's own
_sampled_points() expects.

That source file has a documented ~40% severe-truncation rate in
compression_actions vs. 0% in baseline_replay (truncation_rate_heuristic),
so unlike cycle 53's sample this one is known, from data already on disk,
to span real quality variation before any new judge call is made.

Usage:
  python3 -m \
    real_time_translation.experiments.judge_schematic_adherence_replay_knowncost
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from real_time_translation.experiments.judge_schematic_adherence_replay import (
    SUB_CRITERIA,
    _ols_r_squared,
    _pairwise_correlations,
    judge_decomposed,
)

SOURCE_JSON = Path("experiments/20260916_compression_repeated_sampling_segment5.json")
OUT_JSON = Path("experiments/judge_schematic_adherence_replay_knowncost.json")
OUT_CSV = Path("experiments/judge_schematic_adherence_replay_knowncost.csv")


def _sampled_points(data: dict) -> list[tuple[str, int, str, str]]:
    """Returns (condition, repeat_index, source_full, translation) for every
    repeat of every condition, all against the SAME fixed source text."""
    source_full = data["input"]["target_segment_text"]
    points: list[tuple[str, int, str, str]] = []
    for cond, cond_val in data["results"].items():
        for sample in cond_val["samples"]:
            translation = sample.get("translation")
            if not translation:
                continue
            points.append((cond, sample["repeat_index"], source_full, translation))
    return points


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    points = _sampled_points(data)

    rows: list[dict] = []
    for cond, repeat_index, source_full, translation in points:
        result = judge_decomposed(source_full, translation)
        row = {
            "condition": cond,
            "repeat_index": repeat_index,
            **{c: result.get(c) for c in SUB_CRITERIA},
            "score": result.get("score"),
        }
        rows.append(row)

    complete_rows = [
        r for r in rows if all(r.get(c) is not None for c in (*SUB_CRITERIA, "score"))
    ]

    return {
        "sampled_n": len(points),
        "complete_n": len(complete_rows),
        "pairwise_subcriteria_correlations": _pairwise_correlations(complete_rows),
        "schematic_adherence_r_squared": _ols_r_squared(complete_rows),
        "rows": rows,
        "source": str(source_path),
        "notes": (
            "Follow-up to h-judge-rubric-decomposition-schematic-adherence's "
            "ceiling-effect result "
            "(experiments/judge_schematic_adherence_replay.json). "
            "20 new gemini-3.1-flash-lite calls (10 baseline_replay + 10 "
            "compression_actions repeats of the same fixed source segment), "
            "zero new Deepgram spend -- replay of already-recorded "
            f"translations from {source_path}, which has a documented ~40% "
            "severe-truncation rate in compression_actions "
            "(truncation_rate_heuristic) vs. 0% in baseline_replay, unlike "
            "cycle 53's ceiling-effect sample."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_JSON)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = parser.parse_args()

    result = analyze(args.source)

    with args.out_json.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["condition", "repeat_index", *SUB_CRITERIA, "score"])
        for row in result["rows"]:
            writer.writerow(
                [row["condition"], row["repeat_index"]]
                + [row.get(c) for c in SUB_CRITERIA]
                + [row.get("score")]
            )

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

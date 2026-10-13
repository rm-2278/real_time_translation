"""h-judge-schematic-adherence-sliding-context-windows: third attempt at the
schematic-adherence rubric-decomposition check, this time on multi-segment
sliding-window spans with real surrounding context on both the source and
translation side.

Cycle 53 (judge_schematic_adherence_replay.py) and cycle 54
(judge_schematic_adherence_replay_knowncost.py) both got a degenerate
zero-variance result for two different reasons: cycle 53 sampled FINAL
cumulative multi-batch text where conditions had already converged; cycle 54
fixed that by sampling a condition with a documented fidelity gap, but that
gap lived in a single isolated out-of-context ASR fragment, which the judge
treated as unrecoverable fragment ambiguity rather than a scoreable omission.

This script instead samples 4-consecutive-segment sliding windows (joining
each window's source_text / translation fields with a space) from
experiments/20260916_compression_actions_prompt.json, which holds 23
sequential, ordered segments per condition (baseline_replay,
compression_actions) over the same 120s clip, and whose own prior holistic
judge() call already scored the two conditions differently on real,
non-ceiling grounds (retention_score 75 vs 85, with distinct
dropped_or_distorted lists). Every window has multiple clauses of real
context on both sides, unlike either prior attempt.

Reuses judge_decomposed()/_pairwise_correlations()/_ols_r_squared()/
SUB_CRITERIA from judge_schematic_adherence_replay.py unchanged (same
prompt, same stats, same per-call cost) -- only the point-extraction
differs.

Usage:
  python3 -m \
    real_time_translation.experiments.judge_schematic_adherence_replay_context_windows
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

SOURCE_JSON = Path("experiments/20260916_compression_actions_prompt.json")
OUT_JSON = Path("experiments/judge_schematic_adherence_replay_context_windows.json")
OUT_CSV = Path("experiments/judge_schematic_adherence_replay_context_windows.csv")
WINDOW_SIZE = 4


def _sampled_points(data: dict) -> list[tuple[str, int, str, str]]:
    """Returns (condition, window_start_index, source_full, translation) for
    every WINDOW_SIZE-consecutive-segment sliding window of every condition,
    source_text/translation joined with a space across the window."""
    points: list[tuple[str, int, str, str]] = []
    for cond, cond_val in data["results"].items():
        segments = sorted(cond_val["segments"], key=lambda s: s["index"])
        for start in range(len(segments) - WINDOW_SIZE + 1):
            window = segments[start : start + WINDOW_SIZE]
            source_full = " ".join(s["source_text"] for s in window)
            translation = " ".join(s["translation"] for s in window)
            if not source_full or not translation:
                continue
            points.append((cond, start, source_full, translation))
    return points


def analyze(source_path: Path) -> dict:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    points = _sampled_points(data)

    rows: list[dict] = []
    for cond, window_start, source_full, translation in points:
        result = judge_decomposed(source_full, translation)
        row = {
            "condition": cond,
            "window_start_index": window_start,
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
        "window_size": WINDOW_SIZE,
        "notes": (
            "Third attempt at the schematic-adherence diagnostic, following "
            "cycle 53's ceiling-effect result "
            "(experiments/judge_schematic_adherence_replay.json) and cycle 54's "
            "isolated-fragment ceiling-effect result "
            "(experiments/judge_schematic_adherence_replay_knowncost.json). "
            f"{len(points)} new gemini-3.1-flash-lite calls ({WINDOW_SIZE}-segment "
            "sliding windows, both conditions), zero new Deepgram spend -- "
            f"replay of already-recorded segments from {source_path}, which has a "
            "documented NON-ceiling fidelity difference between conditions "
            "(retention_score 75 baseline_replay / 85 compression_actions, "
            "distinct dropped_or_distorted lists), unlike either prior attempt."
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
        writer.writerow(["condition", "window_start_index", *SUB_CRITERIA, "score"])
        for row in result["rows"]:
            writer.writerow(
                [row["condition"], row["window_start_index"]]
                + [row.get(c) for c in SUB_CRITERIA]
                + [row.get("score")]
            )

    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    main()

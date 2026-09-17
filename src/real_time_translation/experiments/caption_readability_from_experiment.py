"""Score an experiment JSON's captions against real readability standards.

h-backlog-adaptive-compression-budget (research_agent/state/
hypotheses.json) needed an "unreadable fragment" metric that doesn't yet
exist -- a caption whose real on-screen duration (bounded by when the NEXT
caption replaces it, not just its own text length) is too short to read.
That metric already exists in this repo, just never wired to experiment
JSONs directly: `subtitle_formatter.extract_utterance_cues` +
`format_naive`/`format_readable` turn an experiment's event log into real
`Cue`s with wall-clock-accurate start/end times (via `_clip_to_next_start`,
which is exactly the "how long was this actually on screen" signal), and
`readability_metrics.analyze_readability` already scores CPS/duration
violations against Netflix's/BBC's published standards. This module is
the retroactive glue between an experiment JSON and those two existing
tools -- no new metric logic, no new ASR/LLM calls.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from pathlib import Path

from real_time_translation.captions.readability_metrics import (
    ReadabilityReport,
    analyze_readability,
)
from real_time_translation.captions.subtitle_formatter import (
    extract_utterance_cues,
    format_naive,
    format_readable,
)


def analyze_experiment(path: Path, *, mode: str) -> ReadabilityReport | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    raw = extract_utterance_cues(events)
    if not raw:
        return None
    cues = format_naive(raw) if mode == "naive" else format_readable(raw)
    if not cues:
        return None
    return analyze_readability(cues)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument("--mode", choices=["naive", "readable"], default="naive")
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/caption_readability_from_experiment.csv"),
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    rows: list[dict[str, object]] = []
    for path in paths:
        try:
            report = analyze_experiment(path, mode=args.mode)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if report is None:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "experiment_name": data.get("experiment_name", path.stem),
                "json_path": str(path),
                "n_cues": report.n_cues,
                "mean_cps": round(report.mean_cps, 3),
                "cps_violation_rate": round(report.cps_violation_rate, 4),
                "duration_violation_rate": round(report.duration_violation_rate, 4),
                "any_violation_rate": round(report.any_violation_rate, 4),
            }
        )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "experiment_name",
                "json_path",
                "n_cues",
                "mean_cps",
                "cps_violation_rate",
                "duration_violation_rate",
                "any_violation_rate",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"analyzed {len(rows)} experiment(s) -> {args.out_csv}")
    if rows:
        overall = sum(r["duration_violation_rate"] for r in rows) / len(rows)
        print(f"mean duration_violation_rate across corpus: {overall:.4f}")


if __name__ == "__main__":
    main()

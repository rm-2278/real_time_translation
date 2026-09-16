"""Join this repo's separately-computed quality and latency metrics into one
per-experiment table, keyed by `experiment_name`.

Motivation (cycle 15 GENERATE_HYPOTHESES): papi2025-howreal and
polak2026-meta-evaluation-latency-metrics (both already read, see
research_agent/state/papers.json) argue that simultaneous-translation
systems should be evaluated on a *joint* quality-latency curve (as
SimulEval does), not on a single coarse latency number reported next to a
single coarse quality number. This repo already computes several latency
metrics as separate sidecar CSVs (asr_latency_cla.csv, flicker_metrics.csv,
mt_latency_decomposition.csv) plus chrF/xcomet quality scores in
results.csv, but never joins them into one row-per-experiment table -- this
module does exactly that, retroactively, with no new ASR/LLM calls.

Explicitly NOT a reimplementation of Average Lagging / LAAL / YAAL
(token-level source-consumption formulas need per-token source-alignment
data this pipeline's event log does not carry at the granularity those
metrics assume) -- this is a simpler, honest join of the metrics already on
disk, which is enough to let a human eyeball whether a config that "wins"
on latency also "loses" on quality across an existing sweep (e.g. the
endpointing or masking-holdback experiments), which is the actual thing the
literature's complaint is about (a single scalar hiding a real tradeoff).
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _read_csv_by_key(path: Path, key_col: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row[key_col]: row for row in csv.DictReader(f)}


def _from_notes(notes: str, key: str) -> str:
    # results.csv packs some numbers into a free-text "notes" column (e.g.
    # "avg_latency=6.96s max_latency=9.64s avg_mt_latency=1.74s;") rather
    # than a dedicated column.
    for part in notes.split():
        if part.startswith(key + "="):
            return part[len(key) + 1 :].rstrip("s;")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-csv", type=Path, default=Path("experiments/results.csv")
    )
    parser.add_argument(
        "--cla-csv", type=Path, default=Path("experiments/asr_latency_cla.csv")
    )
    parser.add_argument(
        "--flicker-csv", type=Path, default=Path("experiments/flicker_metrics.csv")
    )
    parser.add_argument(
        "--mt-decomposition-csv",
        type=Path,
        default=Path("experiments/mt_latency_decomposition.csv"),
    )
    parser.add_argument(
        "--out-csv", type=Path, default=Path("experiments/quality_latency_joint.csv")
    )
    args = parser.parse_args()

    if not args.results_csv.exists():
        raise SystemExit(f"missing {args.results_csv}")

    cla = _read_csv_by_key(args.cla_csv, "experiment_name")
    flicker = _read_csv_by_key(args.flicker_csv, "experiment_name")
    decomp = _read_csv_by_key(args.mt_decomposition_csv, "experiment_name")

    fieldnames = [
        "experiment_name",
        "domain",
        "chrf_score",
        "avg_end_to_end_latency_seconds",
        "avg_mt_latency_seconds",
        "asr_word_latency_cla_mean_s",
        "translation_ne_char_cross_batch_mean",
        "mt_queue_wait_median_s",
        "mt_stream_duration_mean_s",
    ]

    rows_out: list[dict[str, str]] = []
    with args.results_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["experiment_name"]
            notes = row.get("notes", "")
            rows_out.append(
                {
                    "experiment_name": name,
                    "domain": row.get("domain", ""),
                    "chrf_score": row.get("chrf_score", ""),
                    "avg_end_to_end_latency_seconds": _from_notes(
                        notes, "avg_latency"
                    ),
                    "avg_mt_latency_seconds": _from_notes(notes, "avg_mt_latency"),
                    "asr_word_latency_cla_mean_s": cla.get(name, {}).get(
                        "asr_word_latency_cla_mean_s", ""
                    ),
                    "translation_ne_char_cross_batch_mean": flicker.get(name, {}).get(
                        "translation_ne_char_cross_batch_mean", ""
                    ),
                    "mt_queue_wait_median_s": decomp.get(name, {}).get(
                        "queue_wait_median_s", ""
                    ),
                    "mt_stream_duration_mean_s": decomp.get(name, {}).get(
                        "stream_duration_mean_s", ""
                    ),
                }
            )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"joined {len(rows_out)} experiment row(s) -> {args.out_csv}")
    with_both = [
        r
        for r in rows_out
        if r["chrf_score"]
        and (r["mt_queue_wait_median_s"] or r["asr_word_latency_cla_mean_s"])
    ]
    print(f"{len(with_both)} row(s) have both a quality score and a latency metric")


if __name__ == "__main__":
    main()

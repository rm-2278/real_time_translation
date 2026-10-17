"""Does the batch immediately after a long mid-run silence gap carry an
elevated MT queue-wait? Retroactive, no new ASR/LLM calls.

Motivation (h-mt-queue-wait-post-gap-residual, cycle 62): cycle 61's
`h-ca-al-gap-exclusion-softsegmenter` found that excluding the two outlier
full-lecture runs' long mid-run silence gaps (`simul_latency_metrics`'s
`GAP_EXCLUSION_THRESHOLD_S=10.0`) from the AL ideal-rate line drops their
`ca_al_char_s_gapexcl` substantially but leaves a 45-70s residual that does
not come down into the structurally similar other lecture pair's range, and
speculated the residual could be "a translation backlog that does not fully
drain across/after a long pause" -- connectable to
`h-mt-queue-wait-decomposition`'s own `queue_wait_s` metric
(`mt_latency_decomposition.batch_latencies()`) but never directly measured
there. This module joins the two: for each gap `simul_latency_metrics`
excludes, tag the one batch whose content starts right after it as
"post-gap", and compare its `queue_wait_s` against every other ("baseline")
batch in the same run.

Tagging: `_tag_post_gap_batches()` matches each excluded gap to at most one
batch -- the batch whose `key[0]` (`asr_start_time`) is closest to the gap's
`gap_end`, and only if that distance is within `POST_GAP_TOLERANCE_S`
(1.0s). A gap whose nearest batch start is farther than that, or whose
batch has no resolvable `queue_wait_s` (see
`mt_latency_decomposition._content_ready_lookup()`'s own limitation,
unchanged here), contributes no post-gap data point -- this is expected and
reported as `num_excluded_gaps` vs `num_post_gap_resolved` rather than
silently dropped.

`explanatory_share_of_gapexcl_al`: how much of the run's own
`ca_al_char_s_gapexcl` (from `simul_latency_metrics.analyze_experiment()`,
the residual AL left after gap exclusion) the post-gap queue-wait excess
alone could account for, computed as
`sum(post_gap.queue_wait_s - baseline_queue_wait_mean_s) / ca_al_char_s_gapexcl`
over resolved post-gap batches -- a rough, single-number answer to "does
this effect explain the residual", not a rigorous attribution (it ignores
how the excess at one batch propagates to the settle times of batches after
it, which is left to a future hypothesis if this number turns out
non-trivial).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.mt_latency_decomposition import (
    BatchLatency,
    batch_latencies,
)
from real_time_translation.experiments.simul_latency_metrics import (
    GAP_EXCLUSION_THRESHOLD_S,
    _asr_utterance_intervals,
    _excluded_gaps,
    _ExcludedGap,
)
from real_time_translation.experiments.simul_latency_metrics import (
    analyze_experiment as analyze_al_experiment,
)
from real_time_translation.experiments.stats import mean, median

# How close a batch's asr_start_time must be to a gap's gap_end to count as
# "the batch right after the gap" -- see module docstring's "Tagging" note.
POST_GAP_TOLERANCE_S = 1.0


def _tag_post_gap_batches(
    batches: list[BatchLatency],
    excluded: list[_ExcludedGap],
    tolerance_s: float = POST_GAP_TOLERANCE_S,
) -> dict[tuple[float, float], _ExcludedGap]:
    """For each excluded gap, the single closest batch (by `key[0]` vs.
    `gap_end`) within `tolerance_s`, if any. At most one batch is tagged per
    gap; gaps are non-overlapping and sorted by construction, so no batch
    can be the closest match for two different gaps."""
    tagged: dict[tuple[float, float], _ExcludedGap] = {}
    for gap in excluded:
        best: BatchLatency | None = None
        best_dist: float | None = None
        for b in batches:
            dist = abs(b.key[0] - gap.gap_end)
            if dist <= tolerance_s and (best_dist is None or dist < best_dist):
                best, best_dist = b, dist
        if best is not None:
            tagged[best.key] = gap
    return tagged


@dataclass(frozen=True)
class PostGapQueueWaitReport:
    json_path: str
    experiment_name: str
    num_excluded_gaps: int
    num_post_gap_resolved: int
    post_gap_queue_wait_mean_s: float | None
    post_gap_queue_wait_median_s: float | None
    baseline_queue_wait_mean_s: float | None
    baseline_queue_wait_median_s: float | None
    overall_queue_wait_mean_s: float | None
    overall_queue_wait_median_s: float | None
    excess_queue_wait_total_s: float | None
    ca_al_char_s_gapexcl: float | None
    explanatory_share_of_gapexcl_al: float | None


def analyze_experiment(path: Path) -> PostGapQueueWaitReport | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    if not events:
        return None

    intervals = _asr_utterance_intervals(events)
    excluded = _excluded_gaps(intervals, GAP_EXCLUSION_THRESHOLD_S)
    batches = batch_latencies(events)
    tagged = _tag_post_gap_batches(batches, excluded)

    post_gap_waits = [
        b.queue_wait_s
        for b in batches
        if b.key in tagged and b.queue_wait_s is not None
    ]
    baseline_waits = [
        b.queue_wait_s
        for b in batches
        if b.key not in tagged and b.queue_wait_s is not None
    ]
    overall_waits = [b.queue_wait_s for b in batches if b.queue_wait_s is not None]

    baseline_mean = mean(baseline_waits)
    excess_total: float | None = None
    if post_gap_waits and baseline_mean is not None:
        excess_total = sum(w - baseline_mean for w in post_gap_waits)

    al_report = analyze_al_experiment(path)
    gapexcl_al = al_report.ca_al_char_s_gapexcl if al_report is not None else None
    explanatory_share: float | None = None
    if excess_total is not None and gapexcl_al:
        explanatory_share = excess_total / gapexcl_al

    return PostGapQueueWaitReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_excluded_gaps=len(excluded),
        num_post_gap_resolved=len(post_gap_waits),
        post_gap_queue_wait_mean_s=mean(post_gap_waits),
        post_gap_queue_wait_median_s=median(post_gap_waits),
        baseline_queue_wait_mean_s=baseline_mean,
        baseline_queue_wait_median_s=median(baseline_waits),
        overall_queue_wait_mean_s=mean(overall_waits),
        overall_queue_wait_median_s=median(overall_waits),
        excess_queue_wait_total_s=excess_total,
        ca_al_char_s_gapexcl=gapexcl_al,
        explanatory_share_of_gapexcl_al=explanatory_share,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/post_gap_queue_wait.csv"),
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[PostGapQueueWaitReport] = []
    for path in paths:
        try:
            r = analyze_experiment(path)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if r is not None and r.num_excluded_gaps > 0:
            reports.append(r)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_excluded_gaps",
                "num_post_gap_resolved",
                "post_gap_queue_wait_mean_s",
                "post_gap_queue_wait_median_s",
                "baseline_queue_wait_mean_s",
                "baseline_queue_wait_median_s",
                "overall_queue_wait_mean_s",
                "overall_queue_wait_median_s",
                "excess_queue_wait_total_s",
                "ca_al_char_s_gapexcl",
                "explanatory_share_of_gapexcl_al",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_excluded_gaps,
                    r.num_post_gap_resolved,
                    r.post_gap_queue_wait_mean_s,
                    r.post_gap_queue_wait_median_s,
                    r.baseline_queue_wait_mean_s,
                    r.baseline_queue_wait_median_s,
                    r.overall_queue_wait_mean_s,
                    r.overall_queue_wait_median_s,
                    r.excess_queue_wait_total_s,
                    r.ca_al_char_s_gapexcl,
                    r.explanatory_share_of_gapexcl_al,
                ]
            )

    print(
        f"analyzed {len(reports)} experiment(s) with >=1 excluded gap -> {args.out_csv}"
    )
    for r in reports:
        print(
            f"{r.experiment_name}: {r.num_post_gap_resolved}/{r.num_excluded_gaps} "
            "gaps resolved to a post-gap batch; "
            f"post_gap_mean={r.post_gap_queue_wait_mean_s}, "
            f"baseline_mean={r.baseline_queue_wait_mean_s}, "
            f"excess_total_s={r.excess_queue_wait_total_s}, "
            f"ca_al_char_s_gapexcl={r.ca_al_char_s_gapexcl}, "
            f"explanatory_share={r.explanatory_share_of_gapexcl_al}"
        )


if __name__ == "__main__":
    main()

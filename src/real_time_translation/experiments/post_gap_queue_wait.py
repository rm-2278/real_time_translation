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

Persistence (h-post-gap-queue-wait-persistence, cycle 63): the above
("offset 0" / "post-gap") view tags only the single batch nearest each
gap's `gap_end`. `_tag_offset_batches()` extends this to offsets 1, 2, 3,
... (the 2nd, 3rd, ... batch after the gap, in the same chronological order
`batch_latencies()` returns), stopping before it would overlap the next
gap's own offset-0 anchor. `analyze_persistence()` reports mean
`queue_wait_s` per offset per run and a `cumulative_explanatory_share`
that sums excess queue-wait over every offset found elevated (mean above
that run's offset-0-and-later-excluded baseline), to see whether widening
the accounting beyond offset 0 closes more of `h-mt-queue-wait-post-gap-
residual`'s unexplained 39.7-54.9% remainder, or whether the effect really
is confined to the single batch immediately after the gap.
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


# How many batches after each gap's offset-0 anchor to examine for a
# lingering elevated queue_wait_s -- see module docstring's "Persistence"
# note. Chosen generously (most runs' baseline cadence is well under this
# many batches between gaps); any offset with too few resolved samples
# across all gaps/runs is reported as such, not silently dropped.
MAX_POST_GAP_OFFSET = 5


def _tag_offset_batches(
    batches: list[BatchLatency],
    excluded: list[_ExcludedGap],
    tolerance_s: float = POST_GAP_TOLERANCE_S,
    max_offset: int = MAX_POST_GAP_OFFSET,
) -> dict[tuple[float, float], int]:
    """Like `_tag_post_gap_batches()`, but maps each batch to its integer
    offset (0, 1, 2, ...) from the nearest preceding gap's offset-0 anchor,
    using `batches`' own chronological order for "nearest preceding" and
    "next batch". A gap's offset run stops before it would overlap the next
    gap's own offset-0 anchor (so a short baseline cadence between two
    close gaps never double-tags a batch)."""
    anchor_to_gap = _tag_post_gap_batches(batches, excluded, tolerance_s)
    anchor_indices = sorted(
        i for i, b in enumerate(batches) if b.key in anchor_to_gap
    )
    tagged: dict[tuple[float, float], int] = {}
    for pos, idx in enumerate(anchor_indices):
        next_anchor_idx = (
            anchor_indices[pos + 1] if pos + 1 < len(anchor_indices) else len(batches)
        )
        last_idx = min(idx + max_offset, next_anchor_idx - 1, len(batches) - 1)
        for offset, i in enumerate(range(idx, last_idx + 1)):
            tagged[batches[i].key] = offset
    return tagged


@dataclass(frozen=True)
class PostGapPersistenceReport:
    json_path: str
    experiment_name: str
    num_excluded_gaps: int
    per_offset_queue_wait_mean_s: dict[int, float | None]
    per_offset_n: dict[int, int]
    baseline_queue_wait_mean_s: float | None
    ca_al_char_s_gapexcl: float | None
    cumulative_excess_queue_wait_s: float | None
    cumulative_explanatory_share: float | None
    max_elevated_offset: int | None


def analyze_persistence(path: Path) -> PostGapPersistenceReport | None:
    """Per-offset (0, 1, 2, ...) queue_wait_s breakdown after each excluded
    gap, and how much of `ca_al_char_s_gapexcl` the cumulative excess across
    all elevated offsets explains -- see module docstring's "Persistence"
    note. An offset counts as "elevated" if its mean queue_wait_s exceeds
    the baseline mean; `max_elevated_offset` is the largest such offset
    found, read as "the effect lingers for at least this many batches",
    with the caveat that a small per-offset sample size (`per_offset_n`)
    makes a late offset's mean noisy -- report both, don't just take the
    mean at face value."""
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    if not events:
        return None

    intervals = _asr_utterance_intervals(events)
    excluded = _excluded_gaps(intervals, GAP_EXCLUSION_THRESHOLD_S)
    batches = batch_latencies(events)
    if not excluded:
        return None
    offset_tags = _tag_offset_batches(batches, excluded)

    per_offset_waits: dict[int, list[float]] = {}
    for b in batches:
        offset = offset_tags.get(b.key)
        if offset is not None and b.queue_wait_s is not None:
            per_offset_waits.setdefault(offset, []).append(b.queue_wait_s)

    baseline_waits = [
        b.queue_wait_s
        for b in batches
        if b.key not in offset_tags and b.queue_wait_s is not None
    ]
    baseline_mean = mean(baseline_waits)

    per_offset_mean = {
        offset: mean(waits) for offset, waits in per_offset_waits.items()
    }
    per_offset_n = {offset: len(waits) for offset, waits in per_offset_waits.items()}

    max_elevated_offset: int | None = None
    cumulative_excess: float = 0.0
    if baseline_mean is not None:
        for offset in sorted(per_offset_mean):
            offset_mean = per_offset_mean[offset]
            if offset_mean is None:
                continue
            if offset_mean > baseline_mean:
                max_elevated_offset = offset
                cumulative_excess += sum(
                    w - baseline_mean for w in per_offset_waits[offset]
                )
            elif max_elevated_offset is not None:
                # Stop accumulating once an offset drops back to/below
                # baseline -- a later offset rising again would be a
                # different, unrelated effect, not this gap's decay tail.
                break

    al_report = analyze_al_experiment(path)
    gapexcl_al = al_report.ca_al_char_s_gapexcl if al_report is not None else None
    explanatory_share: float | None = None
    if max_elevated_offset is not None and gapexcl_al:
        explanatory_share = cumulative_excess / gapexcl_al

    return PostGapPersistenceReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_excluded_gaps=len(excluded),
        per_offset_queue_wait_mean_s=per_offset_mean,
        per_offset_n=per_offset_n,
        baseline_queue_wait_mean_s=baseline_mean,
        ca_al_char_s_gapexcl=gapexcl_al,
        cumulative_excess_queue_wait_s=(
            cumulative_excess if max_elevated_offset is not None else None
        ),
        cumulative_explanatory_share=explanatory_share,
        max_elevated_offset=max_elevated_offset,
    )


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
    parser.add_argument(
        "--persistence-out-csv",
        type=Path,
        default=Path("experiments/post_gap_queue_wait_persistence.csv"),
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

    persistence_reports: list[PostGapPersistenceReport] = []
    for path in paths:
        try:
            pr = analyze_persistence(path)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path} (persistence): {exc}")
            continue
        if pr is not None:
            persistence_reports.append(pr)

    max_offset_seen = max(
        (o for pr in persistence_reports for o in pr.per_offset_queue_wait_mean_s),
        default=-1,
    )
    args.persistence_out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.persistence_out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        offset_cols = [f"offset_{i}_mean_s" for i in range(max_offset_seen + 1)]
        offset_n_cols = [f"offset_{i}_n" for i in range(max_offset_seen + 1)]
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_excluded_gaps",
                "baseline_queue_wait_mean_s",
                "max_elevated_offset",
                "cumulative_excess_queue_wait_s",
                "ca_al_char_s_gapexcl",
                "cumulative_explanatory_share",
                *offset_cols,
                *offset_n_cols,
            ]
        )
        for pr in persistence_reports:
            writer.writerow(
                [
                    pr.experiment_name,
                    pr.json_path,
                    pr.num_excluded_gaps,
                    pr.baseline_queue_wait_mean_s,
                    pr.max_elevated_offset,
                    pr.cumulative_excess_queue_wait_s,
                    pr.ca_al_char_s_gapexcl,
                    pr.cumulative_explanatory_share,
                    *[
                        pr.per_offset_queue_wait_mean_s.get(i)
                        for i in range(max_offset_seen + 1)
                    ],
                    *[pr.per_offset_n.get(i) for i in range(max_offset_seen + 1)],
                ]
            )

    print(
        f"analyzed {len(persistence_reports)} experiment(s) for offset persistence "
        f"-> {args.persistence_out_csv}"
    )
    for pr in persistence_reports:
        offsets_str = ", ".join(
            f"offset_{o}={pr.per_offset_queue_wait_mean_s[o]:.2f}s"
            f"(n={pr.per_offset_n[o]})"
            for o in sorted(pr.per_offset_queue_wait_mean_s)
        )
        print(
            f"{pr.experiment_name}: baseline={pr.baseline_queue_wait_mean_s}, "
            f"{offsets_str}; max_elevated_offset={pr.max_elevated_offset}, "
            f"cumulative_excess_s={pr.cumulative_excess_queue_wait_s}, "
            f"cumulative_explanatory_share={pr.cumulative_explanatory_share}"
        )


if __name__ == "__main__":
    main()

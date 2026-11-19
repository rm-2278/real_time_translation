"""Retrofit CAAL's avoidable-vs-unavoidable delay decomposition onto this
pipeline's existing per-batch latency analyses, retroactively, $0, no new
ASR/LLM calls (h-caal-avoidable-unavoidable-al-decomposition, cycle 94).

Motivation: `caal2609-causality-aware-s2st-policy` argues an
average-lagging-style latency number should report only the AVOIDABLE
portion of delay (queueing/scheduling decisions a system could in
principle remove) separately from the UNAVOIDABLE portion (physical/model
latency a causally-correct system would still incur) -- and flags
`simul_latency_metrics.py`'s `ca_al_char_s` as exactly the kind of
conflated metric it argues against. This module retrofits just the
decomposition *principle* onto data this repo already has, joining three
already-tested per-batch analyses on the shared `(asr_start_time,
asr_end_time)` batch key used throughout this codebase:

1. `mt_latency_decomposition.batch_latencies()` -- `queue_wait_s`
   (scheduling/call-start delay: avoidable) and `stream_duration_s` (LLM
   token-generation wall time once the call has started: unavoidable).
2. `utterance_end_mix.py`'s `is_utterance_end` classification rule -- a
   soft-finalized batch (the fixed `deepgram_max_interim_duration` timer
   firing before Deepgram's own endpointing) attributes its own
   `asr_end_time - asr_start_time` span to the avoidable side (a tunable
   constant); a genuine-final batch attributes it to the unavoidable side
   (the speaker's own actual pause).
3. `simul_latency_metrics.py`'s existing `ca_al_char_s` AL computation,
   reused (not re-derived) purely as the cross-reference column alongside
   this module's own `avoidable_fraction` -- this module does NOT attempt
   to algebraically decompose `ca_al_char_s` itself (a mean lag over
   *character positions*, order of magnitude different from a sum of
   per-batch wall-clock seconds); `avoidable_fraction` is a parallel,
   batch-level accounting of where total per-batch delay goes, reported
   next to `ca_al_char_s` for context, not a term-by-term split of it.

Per batch: `avoidable_s = queue_wait_s + (soft-finalized only)
(asr_end_time - asr_start_time)`; `unavoidable_s = stream_duration_s +
(genuine-final only) (asr_end_time - asr_start_time)`. Summed per run:
`avoidable_fraction = avoidable_s_total / (avoidable_s_total +
unavoidable_s_total)`.

Join-key verification (done before trusting it, per PLAYBOOK.md's
standing instruction -- NOT just assumed from the hypothesis text): a
`(asr_start_time, asr_end_time)` key can recur **non-contiguously** within
one run's event list (confirmed empirically: 2/110 experiment JSONs as of
cycle 94, e.g.
`experiments/20260724_llm2024_8_part1_full_baseline_no_dict.json`, has 8
such keys). `mt_latency_decomposition.batch_latencies()` flushes a new
`BatchLatency` every time the key *changes*, so non-contiguous repeats
produce *multiple* entries sharing one key; `utterance_end_mix.
classify_batches()` groups by the key's *dict identity* instead, which
would instead *merge* those occurrences' `is_utterance_end` flags into a
single entry. Zipping the two modules' already-aggregated outputs by key
would therefore silently misattribute flags across occurrences for these
runs. Fix: `_classify_contiguous_batches()` below reuses `classify_batches
()`'s exact single-flag-or-`None` classification *rule*, but grouped the
same positional (flush-on-key-change) way `batch_latencies()` groups, so
the two lists are guaranteed parallel (same length, same key at each
position) by construction -- `analyze_experiment()` still asserts this
explicitly rather than assuming it, since this is exactly the kind of
"verify empirically" case PLAYBOOK.md's GENERATE_HYPOTHESES section warns
about repeatedly.

A batch missing `queue_wait_s` (no identifiable `content_ready_wallclock`
-- the first batch of a run, or a schema gap; see
`mt_latency_decomposition._content_ready_lookup()`) is excluded entirely
from both `avoidable_s_total` and `unavoidable_s_total` for that run,
same precedent as `mt_latency_decomposition.py` itself excluding such
batches from its own mean/median stats -- partially filling in only one
side of the split would bias the fraction. A batch with an *ambiguous*
`is_utterance_end` (its own events disagree, kept as `None` by
`classify_batches()`'s own rule) still contributes its `queue_wait_s`/
`stream_duration_s` terms, just not the `(asr_end_time - asr_start_time)`
duration term on either side, since that term's whole point is to be
assigned by the (undefined, here) classification.
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
    analyze_experiment as analyze_al,
)
from real_time_translation.experiments.stats import mean, median


def _classify_contiguous_batches(
    events: list[dict],
) -> list[tuple[tuple[float, float], bool | None]]:
    """`(key, is_utterance_end)` per *contiguous* run of a translation
    batch key, same classification rule as `utterance_end_mix.
    classify_batches()` (a batch's flag is the single value shared by
    every one of its own events, or `None` if they disagree) but grouped
    positionally (flush on key change) the same way `mt_latency_
    decomposition.batch_latencies()` groups -- see module docstring for
    why this distinction matters."""
    result: list[tuple[tuple[float, float], bool | None]] = []
    current_key: tuple[float, float] | None = None
    flags: set[bool] = set()

    def flush() -> None:
        if current_key is None:
            return
        result.append(
            (current_key, next(iter(flags)) if len(flags) == 1 else None)
        )

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if current_key is None or key != current_key:
            flush()
            current_key = key
            flags = set()
        flags.add(bool(e.get("is_utterance_end", True)))
    flush()
    return result


@dataclass(frozen=True)
class BatchDecomposition:
    key: tuple[float, float]
    is_utterance_end: bool | None
    queue_wait_s: float
    stream_duration_s: float
    duration_s: float
    avoidable_s: float
    unavoidable_s: float


def _decompose_batch(
    lat: BatchLatency, is_end: bool | None
) -> BatchDecomposition | None:
    if lat.queue_wait_s is None:
        return None
    duration = lat.key[1] - lat.key[0]
    avoidable = lat.queue_wait_s + (duration if is_end is False else 0.0)
    unavoidable = lat.stream_duration_s + (duration if is_end is True else 0.0)
    return BatchDecomposition(
        key=lat.key,
        is_utterance_end=is_end,
        queue_wait_s=lat.queue_wait_s,
        stream_duration_s=lat.stream_duration_s,
        duration_s=duration,
        avoidable_s=avoidable,
        unavoidable_s=unavoidable,
    )


def decompose_batches(events: list[dict]) -> tuple[list[BatchDecomposition], int, int]:
    """Returns `(decompositions, num_excluded_no_queue_wait,
    num_ambiguous_utterance_end)`."""
    latencies = batch_latencies(events)
    classified = _classify_contiguous_batches(events)
    if len(latencies) != len(classified):
        raise ValueError(
            f"batch_latencies() produced {len(latencies)} batches but the "
            f"positional is_utterance_end classification produced "
            f"{len(classified)} -- grouping mismatch, see module docstring"
        )
    decomps: list[BatchDecomposition] = []
    num_excluded = 0
    num_ambiguous = 0
    for lat, (key, is_end) in zip(latencies, classified, strict=True):
        if lat.key != key:
            raise ValueError(
                f"batch key mismatch at matching position: "
                f"{lat.key!r} (batch_latencies) != {key!r} "
                "(is_utterance_end classification) -- see module docstring"
            )
        if is_end is None:
            num_ambiguous += 1
        d = _decompose_batch(lat, is_end)
        if d is None:
            num_excluded += 1
            continue
        decomps.append(d)
    return decomps, num_excluded, num_ambiguous


@dataclass(frozen=True)
class CaalDecompositionReport:
    json_path: str
    experiment_name: str
    num_batches_total: int
    num_batches_excluded_no_queue_wait: int
    num_batches_ambiguous_utterance_end: int
    avoidable_s_total: float
    unavoidable_s_total: float
    avoidable_fraction: float | None
    ca_al_char_s: float | None


def analyze_experiment(path: Path) -> CaalDecompositionReport | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    if not events:
        return None
    decomps, num_excluded, num_ambiguous = decompose_batches(events)
    if not decomps:
        return None

    avoidable_total = sum(d.avoidable_s for d in decomps)
    unavoidable_total = sum(d.unavoidable_s for d in decomps)
    denom = avoidable_total + unavoidable_total
    avoidable_fraction = (avoidable_total / denom) if denom > 0 else None

    al_report = analyze_al(path)
    ca_al_char_s = al_report.ca_al_char_s if al_report is not None else None

    return CaalDecompositionReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_batches_total=num_excluded + len(decomps),
        num_batches_excluded_no_queue_wait=num_excluded,
        num_batches_ambiguous_utterance_end=num_ambiguous,
        avoidable_s_total=avoidable_total,
        unavoidable_s_total=unavoidable_total,
        avoidable_fraction=avoidable_fraction,
        ca_al_char_s=ca_al_char_s,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument(
        "--out-csv", type=Path, default=Path("experiments/caal_decomposition.csv")
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[CaalDecompositionReport] = []
    for path in paths:
        try:
            r = analyze_experiment(path)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        if r is not None:
            reports.append(r)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_batches_total",
                "num_batches_excluded_no_queue_wait",
                "num_batches_ambiguous_utterance_end",
                "avoidable_s_total",
                "unavoidable_s_total",
                "avoidable_fraction",
                "ca_al_char_s",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_batches_total,
                    r.num_batches_excluded_no_queue_wait,
                    r.num_batches_ambiguous_utterance_end,
                    r.avoidable_s_total,
                    r.unavoidable_s_total,
                    r.avoidable_fraction,
                    r.ca_al_char_s,
                ]
            )

    print(f"analyzed {len(reports)} experiment(s) -> {args.out_csv}")
    fractions = [
        r.avoidable_fraction for r in reports if r.avoidable_fraction is not None
    ]
    if fractions:
        print(
            f"avoidable_fraction across {len(fractions)} run(s): "
            f"mean={mean(fractions):.3f} median={median(fractions):.3f} "
            f"min={min(fractions):.3f} max={max(fractions):.3f}"
        )


if __name__ == "__main__":
    main()

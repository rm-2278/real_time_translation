"""Decompose per-batch translation latency into "queue wait" vs "LLM stream
duration", retroactively from the timestamped event log every experiment
JSON already contains under `results.events` -- no new ASR/LLM calls needed.

Motivation (cycle 15 GENERATE_HYPOTHESES, human asked "latency is still
large, what can be done"): `avg_mt_latency_seconds` in results.csv is a
single coarse number, and prior cycles (h-cla-asr-latency-metric,
h-asr-final-emission-latency) already showed the *ASR* side of end-to-end
latency is dominated by `deepgram_max_interim_duration`'s fixed 2.5s
soft-finalize timer rather than the swept `deepgram_endpointing` value. This
module asks the analogous question for the *translation* side: once a
batch's audio content is available, how much of the wait before translated
text appears on screen is (a) queueing/call-start delay (time from content
being ASR-ready to the LLM's first streamed character) versus (b) the LLM
actually streaming its answer (first character to last character)? This
matters because the two have very different fixes: (a) would point at
scheduling/rate-limit/backpressure work, (b) would point at model-choice or
prompt-shortening work (e.g. a faster "draft" model).

For each translation batch (grouped by `(asr_start_time, asr_end_time)`,
same key `flicker_metrics.group_translation` uses):
  content_ready_wallclock = playback_offset of the first `asr_interim` event
    whose own `asr_end_time` equals this batch's `asr_end_time` -- i.e. the
    wall-clock moment this pipeline first saw the audio content this batch
    translates.
  queue_wait_s = (batch's first translation_partial/translation_complete
    event's playback_offset) - content_ready_wallclock
  stream_duration_s = (batch's last event's playback_offset) - (batch's
    first event's playback_offset)
Both are None for a batch whose content_ready timestamp can't be found
(e.g. the very first batch of a run, or a schema gap).
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatchLatency:
    key: tuple[float, float]
    content_ready_wallclock: float | None
    first_event_wallclock: float
    last_event_wallclock: float
    queue_wait_s: float | None
    stream_duration_s: float


def _content_ready_lookup(events: list[dict]) -> dict[float, float]:
    """asr_end_time -> playback_offset of the FIRST asr_interim event that
    reported that end_time (interims are emitted in increasing asr_end_time
    order, so "first occurrence" is when the pipeline first became aware of
    audio content up to that point)."""
    lookup: dict[float, float] = {}
    for e in events:
        if e.get("kind") != "asr_interim":
            continue
        end_time = e.get("asr_end_time")
        if end_time is None or end_time in lookup:
            continue
        lookup[end_time] = e.get("playback_offset", 0.0)
    return lookup


def batch_latencies(events: list[dict]) -> list[BatchLatency]:
    content_ready = _content_ready_lookup(events)
    results: list[BatchLatency] = []
    current_key: tuple[float, float] | None = None
    offsets: list[float] = []

    def flush() -> None:
        if not offsets or current_key is None:
            return
        ready = content_ready.get(current_key[1])
        queue_wait = (offsets[0] - ready) if ready is not None else None
        results.append(
            BatchLatency(
                key=current_key,
                content_ready_wallclock=ready,
                first_event_wallclock=offsets[0],
                last_event_wallclock=offsets[-1],
                queue_wait_s=queue_wait,
                stream_duration_s=offsets[-1] - offsets[0],
            )
        )

    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if current_key is None or key != current_key:
            flush()
            current_key = key
            offsets = []
        offsets.append(e.get("playback_offset", 0.0))
    flush()
    return results


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class DecompositionReport:
    json_path: str
    experiment_name: str
    num_batches: int
    num_batches_with_queue_wait: int
    queue_wait_mean_s: float | None
    queue_wait_median_s: float | None
    stream_duration_mean_s: float | None
    stream_duration_median_s: float | None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def analyze_experiment(path: Path) -> DecompositionReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    batches = batch_latencies(events)
    queue_waits = [b.queue_wait_s for b in batches if b.queue_wait_s is not None]
    streams = [b.stream_duration_s for b in batches]
    return DecompositionReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        num_batches=len(batches),
        num_batches_with_queue_wait=len(queue_waits),
        queue_wait_mean_s=_mean(queue_waits),
        queue_wait_median_s=_median(queue_waits),
        stream_duration_mean_s=_mean(streams),
        stream_duration_median_s=_median(streams),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None)
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/mt_latency_decomposition.csv"),
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(p) for p in sorted(glob.glob(pattern)))
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[DecompositionReport] = []
    all_queue_waits: list[float] = []
    all_streams: list[float] = []
    for path in paths:
        try:
            r = analyze_experiment(path)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")
            continue
        reports.append(r)
        events = json.loads(path.read_text(encoding="utf-8")).get("results", {}).get(
            "events", []
        )
        for b in batch_latencies(events):
            if b.queue_wait_s is not None:
                all_queue_waits.append(b.queue_wait_s)
            all_streams.append(b.stream_duration_s)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "num_batches",
                "num_batches_with_queue_wait",
                "queue_wait_mean_s",
                "queue_wait_median_s",
                "stream_duration_mean_s",
                "stream_duration_median_s",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.num_batches,
                    r.num_batches_with_queue_wait,
                    r.queue_wait_mean_s,
                    r.queue_wait_median_s,
                    r.stream_duration_mean_s,
                    r.stream_duration_median_s,
                ]
            )

    print(f"analyzed {len(reports)} experiment(s) -> {args.out_csv}")
    if all_queue_waits:
        print(
            f"queue_wait_s across {len(all_queue_waits)} batch(es): "
            f"mean={_mean(all_queue_waits):.3f} median={_median(all_queue_waits):.3f} "
            f"min={min(all_queue_waits):.3f} max={max(all_queue_waits):.3f}"
        )
    if all_streams:
        print(
            f"stream_duration_s across {len(all_streams)} batch(es): "
            f"mean={_mean(all_streams):.3f} median={_median(all_streams):.3f} "
            f"min={min(all_streams):.3f} max={max(all_streams):.3f}"
        )


if __name__ == "__main__":
    main()

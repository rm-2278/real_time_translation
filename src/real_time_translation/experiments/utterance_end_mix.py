"""Genuine-endpointing-final vs soft-finalize mix (h-asr-final-emission-latency).

`h-cla-asr-latency-metric` found ASR word latency (via Continuous Levenshtein
Alignment against a gold transcript) essentially flat across the entire
`deepgram_endpointing` sweep (300-2000ms). `h-endpointing-connection-verify`
then ruled out a wiring bug -- the config value really does reach Deepgram's
`connect()` call. That leaves the open question of *why* sweeping it has so
little effect.

`deepgram_client.py` distinguishes two ways a translation-triggering batch
gets cut off:

1. A **genuine** Deepgram-native finalize (`is_utterance_end=True`): Deepgram
   itself decided the utterance ended, via its own `is_final`/`UtteranceEnd`
   message -- this is exactly the mechanism `deepgram_endpointing` (a silence
   duration in ms) controls.
2. A **soft finalize** (`is_utterance_end=False`), forced early by the
   constant `deepgram_max_interim_duration` timer (2.5s in these experiments)
   when continuous speech runs well past `endpointing`/`utterance_end_ms`
   without Deepgram ever emitting its own `is_final` -- see
   `DeepgramTranscriber._periodic_soft_finalize_loop` /
   `_soft_finalize_pending`.

If most batches in the endpointing sweep are soft-finalized regardless of
the configured `deepgram_endpointing` value, that would explain the flat CLA
result mechanistically: the fixed 2.5s timer pre-empts Deepgram's own
endpointing decision before it gets a chance to matter, for continuous
speech. This is a $0 retroactive analysis -- `is_utterance_end` is already
logged on every `results.events` entry, no new recording needed.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from dataclasses import dataclass
from pathlib import Path

from real_time_translation.experiments.stats import mean, median


@dataclass(frozen=True)
class BatchClassification:
    key: tuple[float, float]
    is_utterance_end: bool | None  # None if the batch's events disagree
    duration: float  # asr_end_time - asr_start_time


def classify_batches(events: list[dict]) -> list[BatchClassification]:
    """Group translation_partial/translation_complete events into batches
    (same (asr_start_time, asr_end_time) key as flicker_metrics.group_
    translation) and classify each by its is_utterance_end flag.

    is_utterance_end is expected to be constant across every event within
    one batch (it comes from a single TranslationResult per batch, just
    streamed token-by-token) -- if a batch's events disagree, that's a real
    anomaly worth surfacing rather than silently picking one, so it's kept
    as `None` and excluded from the genuine/soft counts.
    """
    by_key: dict[tuple[float, float], set[bool]] = {}
    order: list[tuple[float, float]] = []
    for e in events:
        if e.get("kind") not in ("translation_partial", "translation_complete"):
            continue
        key = (e.get("asr_start_time", 0.0), e.get("asr_end_time", 0.0))
        if key not in by_key:
            by_key[key] = set()
            order.append(key)
        by_key[key].add(bool(e.get("is_utterance_end", True)))

    results: list[BatchClassification] = []
    for key in order:
        flags = by_key[key]
        is_end = next(iter(flags)) if len(flags) == 1 else None
        results.append(
            BatchClassification(
                key=key, is_utterance_end=is_end, duration=key[1] - key[0]
            )
        )
    return results


@dataclass(frozen=True)
class ExperimentMixReport:
    json_path: str
    experiment_name: str
    deepgram_endpointing: int | None
    deepgram_max_interim_duration: float | None
    num_batches: int
    num_genuine_final: int
    num_soft_finalized: int
    num_ambiguous: int
    pct_soft_finalized: float | None
    genuine_final_duration_mean: float | None
    genuine_final_duration_median: float | None
    soft_finalized_duration_mean: float | None
    soft_finalized_duration_median: float | None


def analyze_experiment(path: Path) -> ExperimentMixReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("results", {}).get("events", [])
    config = data.get("config", {})
    batches = classify_batches(events)

    genuine = [b for b in batches if b.is_utterance_end is True]
    soft = [b for b in batches if b.is_utterance_end is False]
    ambiguous = [b for b in batches if b.is_utterance_end is None]
    classified = len(genuine) + len(soft)

    return ExperimentMixReport(
        json_path=str(path),
        experiment_name=data.get("experiment_name", path.stem),
        deepgram_endpointing=config.get("deepgram_endpointing"),
        deepgram_max_interim_duration=config.get("deepgram_max_interim_duration"),
        num_batches=len(batches),
        num_genuine_final=len(genuine),
        num_soft_finalized=len(soft),
        num_ambiguous=len(ambiguous),
        pct_soft_finalized=(len(soft) / classified * 100.0) if classified else None,
        genuine_final_duration_mean=mean([b.duration for b in genuine]),
        genuine_final_duration_median=median([b.duration for b in genuine]),
        soft_finalized_duration_mean=mean([b.duration for b in soft]),
        soft_finalized_duration_median=median([b.duration for b in soft]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=None,
        help="Experiment JSON file(s) or glob(s). Defaults to experiments/*.json",
    )
    parser.add_argument(
        "--out-csv",
        type=Path,
        default=Path("experiments/utterance_end_mix.csv"),
        help="Sidecar CSV to write (kept separate from results.csv, same "
        "precedent as flicker_metrics.py/asr_latency_cla.py)",
    )
    args = parser.parse_args()

    patterns = args.paths or ["experiments/*.json"]
    paths: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        paths.extend(Path(p) for p in matched)
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    reports: list[ExperimentMixReport] = []
    for path in paths:
        try:
            reports.append(analyze_experiment(path))
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}")

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "experiment_name",
                "json_path",
                "deepgram_endpointing",
                "deepgram_max_interim_duration",
                "num_batches",
                "num_genuine_final",
                "num_soft_finalized",
                "num_ambiguous",
                "pct_soft_finalized",
                "genuine_final_duration_mean",
                "genuine_final_duration_median",
                "soft_finalized_duration_mean",
                "soft_finalized_duration_median",
            ]
        )
        for r in reports:
            writer.writerow(
                [
                    r.experiment_name,
                    r.json_path,
                    r.deepgram_endpointing,
                    r.deepgram_max_interim_duration,
                    r.num_batches,
                    r.num_genuine_final,
                    r.num_soft_finalized,
                    r.num_ambiguous,
                    r.pct_soft_finalized,
                    r.genuine_final_duration_mean,
                    r.genuine_final_duration_median,
                    r.soft_finalized_duration_mean,
                    r.soft_finalized_duration_median,
                ]
            )

    print(f"analyzed {len(reports)} experiment(s) -> {args.out_csv}")

    sweep = [r for r in reports if "chunk_latency_sweep" in r.experiment_name]
    if sweep:
        print(
            f"\nchunk_latency_sweep{{,2}} endpointing-ms family "
            f"({len(sweep)} files):"
        )
        sweep_sorted = sorted(
            sweep, key=lambda r: (r.experiment_name, r.deepgram_endpointing or 0)
        )
        for r in sweep_sorted:
            print(
                f"  {r.experiment_name}: endpointing={r.deepgram_endpointing}ms  "
                f"batches={r.num_batches}  soft%={r.pct_soft_finalized:.1f}  "
                f"genuine_dur_mean={r.genuine_final_duration_mean}  "
                f"soft_dur_mean={r.soft_finalized_duration_mean}"
            )
        overall_soft_pct = mean(
            [r.pct_soft_finalized for r in sweep if r.pct_soft_finalized is not None]
        )
        if overall_soft_pct is not None:
            print(f"  mean pct_soft_finalized across sweep: {overall_soft_pct:.1f}%")


if __name__ == "__main__":
    main()

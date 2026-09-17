"""Run a reproducible local-video transcription/translation experiment.

Unlike youtube_segment.py (which pulls audio from YouTube), this runner
takes a local video/audio file already on disk. It streams the file's
audio through the pipeline at real playback speed (by default) and, most
importantly, records the true wall-clock arrival time of every ASR and
translation event relative to when playback started. That's what lets us
compute honest end-to-end latency (and later, burn in captions at the
time they actually arrived, not resynced to when the speaker said them).

Usage:
  uv run real-time-translation-exp-video \
    --input "video/LLM2024_8_part1.mp4" \
    --start 3:00 --duration 90 \
    --name baseline_clip

Notes:
- Requires ffmpeg installed on the host.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from real_time_translation.audio.capture import QueueAudioCapture
from real_time_translation.config import Config
from real_time_translation.experiments.glossary_metrics import (
    compute_glossary_adherence,
)
from real_time_translation.pipeline import TranslationPipeline, TranslationResult


def _parse_time(value: str) -> float:
    """Parse a time string into seconds. Accepts seconds, MM:SS, or HH:MM:SS."""
    value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)

    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"Invalid time format: {value!r}")

    numbers = [float(p) for p in parts]
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds

    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "experiment"


@dataclass
class TimedEvent:
    """A single ASR/translation event with true wall-clock arrival time."""

    # "asr_interim" | "asr_final" | "translation_partial" | "translation_complete"
    kind: str
    playback_offset: float  # seconds since streaming started (== video position)
    wall_clock_lag: float  # playback_offset - asr_start_time, once ASR has started
    text: str
    is_final: bool
    asr_start_time: float | None = None
    asr_end_time: float | None = None
    confidence: float = 0.0
    # Source (ASR) text paired with `text` (the translation) for
    # translation_partial/translation_complete events. NOT the same as
    # this batch's new ASR fragment alone -- TranslationResult.original_text
    # is `pipeline._emit_batch_result`'s `acc_source`, i.e. the WHOLE
    # utterance's accumulated source up through this batch, so consecutive
    # same-utterance events' original_text values are prefix-nested and a
    # given batch's own new-fragment text can be recovered by stripping the
    # previous batch's original_text as a prefix (see
    # research_agent/state/hypotheses.json h-gemini-only-masking-replay,
    # which needs exactly this to replay masking-holdback without new
    # Deepgram calls -- added retroactively cycle 5, 2026-09-09; older
    # experiment JSONs predate this field and have it empty).
    original_text: str = ""
    # True once the underlying utterance has actually ended (vs. a
    # soft-finalized mid-utterance chunk with more still coming -- see
    # TranslationResult.is_utterance_end). Consumers rendering these events
    # as captions use this to hold a just-completed sentence on screen
    # longer before cutting to the next one.
    is_utterance_end: bool = True


@dataclass
class SegmentRecord:
    """One completed (ASR-final + translation-complete) utterance."""

    asr_start_time: float | None
    asr_end_time: float | None
    asr_final_arrival: float  # wall-clock offset when ASR finalized this segment
    translation_complete_arrival: float  # wall-clock offset when translation finished
    translation_first_token_arrival: float | None  # for TTFT-style measurement
    end_to_end_latency: float  # translation_complete_arrival - asr_start_time
    mt_latency: float  # translation_complete_arrival - asr_final_arrival
    asr: str
    translation: str
    confidence: float


async def _run_ffmpeg_pcm(
    *,
    input_path: str,
    start_seconds: float,
    duration_seconds: float | None,
    sample_rate: int,
    channels: int,
) -> asyncio.subprocess.Process:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start_seconds}"]
    cmd += ["-i", input_path]
    if duration_seconds is not None:
        cmd += ["-t", f"{duration_seconds}"]
    cmd += [
        "-vn",
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]

    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _read_all_stderr(proc: asyncio.subprocess.Process) -> str:
    if proc.stderr is None:
        return ""
    data = await proc.stderr.read()
    return data.decode("utf-8", errors="replace")


def _ensure_csv_header(csv_path: Path, header: list[str]) -> None:
    """Migrate an existing results.csv to a new (superset) header in place.

    Both experiment runners append to the same results.csv; if one adds a
    metric column the other doesn't know about yet, the physical header
    would drift out of sync with later-appended rows' column order.
    Rewriting preserves old rows (missing new columns read back as "").
    """
    if not csv_path.exists():
        return

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_header = reader.fieldnames
        if existing_header is None or list(existing_header) == header:
            return
        rows = list(reader)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})


def _maybe_compute_chrf(
    *, hypothesis: str, reference_text_path: Path | None
) -> float | None:
    if reference_text_path is None:
        return None
    reference = reference_text_path.read_text(encoding="utf-8").strip()
    if not reference:
        return None
    try:
        from sacrebleu.metrics import CHRF  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "sacrebleu is not installed. Run: uv sync --extra experiments"
        ) from exc
    metric = CHRF(word_order=2)
    return float(metric.corpus_score([hypothesis], [[reference]]).score)


async def run_experiment(
    *,
    input_path: str,
    start_seconds: float,
    duration_seconds: float | None,
    experiment_name: str,
    domain: str | None,
    notes: str | None,
    reference_text_path: Path | None,
    chunk_ms: int,
    speed: float,
    endpointing: int | None = None,
) -> tuple[Path, Path]:
    start_wall = time.time()

    config = Config.from_env(require_zoom=False)
    if endpointing is not None:
        config.deepgram_endpointing = endpointing
    capture = QueueAudioCapture(max_queue_size=2000)
    pipeline = TranslationPipeline(config=config, audio_capture=capture)

    events: list[TimedEvent] = []
    # Track open segments by utterance_id (not start_time -- a soft-
    # finalized utterance spans multiple separate translation calls, each
    # with its own start_time, but shares one utterance_id) so we can pair
    # the asr_final event with its eventual translation_complete event even
    # when they arrive via different calls.
    open_segments: dict[int, dict[str, Any]] = {}
    completed: list[SegmentRecord] = []

    playback_start: float | None = None

    def playback_offset() -> float:
        assert playback_start is not None
        return time.time() - playback_start

    def on_result(result: TranslationResult) -> None:
        offset = playback_offset()
        lag = (
            offset - result.start_time
            if result.start_time is not None
            else float("nan")
        )

        if not result.is_final:
            events.append(
                TimedEvent(
                    kind="asr_interim",
                    playback_offset=offset,
                    wall_clock_lag=lag,
                    text=result.original_text,
                    is_final=False,
                    asr_start_time=result.start_time,
                    asr_end_time=result.end_time,
                    confidence=result.confidence,
                    original_text=result.original_text,
                )
            )
            return

        key = result.utterance_id

        if not result.is_translation_complete:
            events.append(
                TimedEvent(
                    kind="translation_partial",
                    playback_offset=offset,
                    wall_clock_lag=lag,
                    text=result.translated_text,
                    is_final=True,
                    asr_start_time=result.start_time,
                    asr_end_time=result.end_time,
                    confidence=result.confidence,
                    is_utterance_end=result.is_utterance_end,
                    original_text=result.original_text,
                )
            )
            seg = open_segments.setdefault(
                key,
                {
                    "asr_final_arrival": offset,
                    "first_token_arrival": offset,
                    "asr_text": result.original_text,
                    "start_time": result.start_time,
                    "end_time": result.end_time,
                    "confidence": result.confidence,
                },
            )
            seg.setdefault("first_token_arrival", offset)
            return

        # Translation call complete -- but if this is a soft-finalized
        # mid-utterance chunk (see deepgram_max_interim_duration), more
        # text for the same utterance_id is still coming via a later,
        # separate call. Recording it now as its own completed segment
        # would duplicate it against the eventual true-end result (whose
        # translated_text is the pipeline-accumulated superset of this
        # one) -- so keep it open and just refresh the tracked text/timing
        # instead of finalizing.
        events.append(
            TimedEvent(
                kind="translation_complete",
                playback_offset=offset,
                wall_clock_lag=lag,
                text=result.translated_text,
                is_final=True,
                asr_start_time=result.start_time,
                asr_end_time=result.end_time,
                confidence=result.confidence,
                is_utterance_end=result.is_utterance_end,
                original_text=result.original_text,
            )
        )
        if not result.is_utterance_end:
            seg = open_segments.setdefault(
                key,
                {
                    "asr_final_arrival": offset,
                    "first_token_arrival": offset,
                    "asr_text": result.original_text,
                    "start_time": result.start_time,
                    "end_time": result.end_time,
                    "confidence": result.confidence,
                },
            )
            seg["asr_text"] = result.original_text
            seg["end_time"] = result.end_time
            return

        if not result.translated_text:
            open_segments.pop(key, None)
            return

        seg = open_segments.pop(
            key,
            {
                "asr_final_arrival": offset,
                "first_token_arrival": offset,
                "asr_text": result.original_text,
                "start_time": result.start_time,
                "end_time": result.end_time,
                "confidence": result.confidence,
            },
        )
        asr_start = seg["start_time"]
        e2e_latency = (
            (offset - asr_start) if asr_start is not None else float("nan")
        )
        completed.append(
            SegmentRecord(
                asr_start_time=asr_start,
                asr_end_time=seg["end_time"],
                asr_final_arrival=seg["asr_final_arrival"],
                translation_complete_arrival=offset,
                translation_first_token_arrival=seg["first_token_arrival"],
                end_to_end_latency=e2e_latency,
                mt_latency=offset - seg["asr_final_arrival"],
                asr=seg["asr_text"],
                translation=result.translated_text,
                confidence=seg["confidence"],
            )
        )

    pipeline.set_callback(on_result)

    input_file = Path(input_path)
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    sample_rate = 16000
    channels = 1
    bytes_per_sample = 2
    chunk_bytes = int(sample_rate * channels * bytes_per_sample * (chunk_ms / 1000.0))
    if chunk_bytes <= 0:
        raise ValueError("chunk_ms too small")

    proc = await _run_ffmpeg_pcm(
        input_path=str(input_file),
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        channels=channels,
    )
    if proc.stdout is None:
        raise RuntimeError("Failed to capture ffmpeg stdout")

    stderr_task = asyncio.create_task(_read_all_stderr(proc))

    await pipeline.start()
    playback_start = time.time()
    try:
        # Fixed-schedule pacing: target the wall-clock time each chunk
        # *should* have been sent by (chunk_index * interval), not a fresh
        # `interval`-long sleep after each chunk. A sleep-after-each-chunk
        # loop drifts under load -- Deepgram's listener and concurrent
        # Gemini translation calls compete for the same event loop, so any
        # single sleep() running a bit long is never recovered, and over
        # thousands of chunks that compounds into minutes of accumulated
        # lag (observed: ~100s of drift over a 2-minute clip). Racing
        # toward a fixed schedule self-corrects: a late chunk gets sent
        # immediately (sleep_for <= 0) instead of adding its lateness on
        # top of the next interval.
        interval = (chunk_ms / 1000.0) / speed if speed > 0 else 0.0
        chunk_index = 0
        loop = asyncio.get_event_loop()
        stream_start = loop.time()
        while True:
            data = await proc.stdout.read(chunk_bytes)
            if not data:
                break
            capture.push_audio(data)
            chunk_index += 1

            if interval > 0:
                target = stream_start + chunk_index * interval
                sleep_for = target - loop.time()
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

        await proc.wait()
        stderr_text = await stderr_task
        if proc.returncode and proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}):\n{stderr_text}")

        await pipeline._audio_capture.stop()
        await pipeline._transcriber.finalize()

        # Give the translation workers time to flush remaining results.
        stable_seconds = 0
        last_count = len(completed)
        for _ in range(180):
            await asyncio.sleep(1)
            if len(completed) == last_count:
                stable_seconds += 1
            else:
                stable_seconds = 0
                last_count = len(completed)
            if stable_seconds >= 15:
                break
    finally:
        await pipeline.stop()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    full_asr = "\n".join(r.asr for r in completed)
    full_translation = "\n".join(r.translation for r in completed)

    chrf_score = _maybe_compute_chrf(
        hypothesis=full_translation, reference_text_path=reference_text_path
    )

    glossary_result = compute_glossary_adherence(
        pipeline._translator.dictionary,
        source_text=full_asr,
        hypothesis_text=full_translation,
    )

    avg_conf = (
        sum(r.confidence for r in completed) / len(completed) if completed else 0.0
    )
    latencies = [
        r.end_to_end_latency
        for r in completed
        if r.end_to_end_latency == r.end_to_end_latency  # filters out NaN
    ]
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    max_latency = max(latencies) if latencies else None
    mt_latencies = [r.mt_latency for r in completed]
    avg_mt_latency = sum(mt_latencies) / len(mt_latencies) if mt_latencies else None

    now = datetime.now(UTC)
    date_str = now.date().isoformat()
    stamp = now.strftime("%Y%m%d")
    slug = _slugify(experiment_name)

    experiments_dir = Path("experiments")
    experiments_dir.mkdir(parents=True, exist_ok=True)

    json_path = experiments_dir / f"{stamp}_{slug}.json"
    csv_path = experiments_dir / "results.csv"

    payload: dict[str, Any] = {
        "date": date_str,
        "experiment_name": experiment_name,
        "domain": domain,
        "input": {
            "type": "local_video",
            "path": str(input_file),
            "start_seconds": start_seconds,
            "duration_seconds": duration_seconds,
        },
        "models": {
            "deepgram_model": config.deepgram_model,
            "deepgram_language": config.deepgram_language,
            "llm_provider": config.llm_provider,
            "llm_model": config.gemini_model
            if config.llm_provider == "gemini"
            else config.openai_model,
            "gemini_thinking_budget": config.gemini_thinking_budget,
        },
        "config": {
            "source_language": config.source_language,
            "target_language": config.target_language,
            "context_window_size": config.context_window_size,
            "translation_queue_size": config.translation_queue_size,
            "translation_workers": config.translation_workers,
            "gemini_rpm_limit": config.gemini_rpm_limit,
            "deepgram_endpointing": config.deepgram_endpointing,
            "deepgram_utterance_end_ms": config.deepgram_utterance_end_ms,
            "deepgram_max_interim_duration": config.deepgram_max_interim_duration,
            "masking_holdback_words": config.masking_holdback_words,
            "anchor_continuation_translation": config.anchor_continuation_translation,
            "localagreement_commit_enabled": config.localagreement_commit_enabled,
            "asr_confidence_early_commit_threshold": (
                config.asr_confidence_early_commit_threshold
            ),
            "asr_confidence_early_commit_min_elapsed": (
                config.asr_confidence_early_commit_min_elapsed
            ),
            "semantic_completeness_gating_enabled": (
                config.semantic_completeness_gating_enabled
            ),
            "semantic_gating_min_elapsed": config.semantic_gating_min_elapsed,
            "semantic_gating_check_interval": config.semantic_gating_check_interval,
            "reading_speed_budget_translation": config.reading_speed_budget_translation,
            "reading_speed_chars_per_sec": config.reading_speed_chars_per_sec,
            "compression_actions_prompt_enabled": config.compression_actions_prompt_enabled,
            "monotonic_interim_translation_enabled": (
                config.monotonic_interim_translation_enabled
            ),
            "domain_packs": config.domain_packs,
            "dictionary_size": len(pipeline._translator.dictionary),
        },
        "results": {
            "segment_count": len(completed),
            "avg_confidence": avg_conf,
            "avg_end_to_end_latency_seconds": avg_latency,
            "max_end_to_end_latency_seconds": max_latency,
            "avg_mt_latency_seconds": avg_mt_latency,
            "segments": [asdict(r) for r in completed],
            "events": [asdict(e) for e in events],
            "full_asr": full_asr,
            "full_translation": full_translation,
        },
        "metrics": {
            "chrf_score": chrf_score,
            "xcomet_score": None,
            "glossary_adherence_rate": glossary_result.rate,
            "glossary_expected_terms": glossary_result.expected_terms,
            "glossary_matched_terms": glossary_result.matched_terms,
            "glossary_missed_terms": glossary_result.missed_terms,
        },
        "notes": notes or "",
        "created_at_utc": now.isoformat(),
        "elapsed_seconds": round(time.time() - start_wall, 3),
    }

    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    header = [
        "date",
        "experiment_name",
        "domain",
        "url",
        "start_seconds",
        "end_seconds",
        "deepgram_model",
        "llm_provider",
        "llm_model",
        "segment_count",
        "avg_confidence",
        "chrf_score",
        "xcomet_score",
        "glossary_adherence_rate",
        "glossary_expected_terms",
        "glossary_matched_terms",
        "notes",
        "json_path",
    ]
    row = {
        "date": date_str,
        "experiment_name": experiment_name,
        "domain": domain or "",
        "url": str(input_file),
        "start_seconds": f"{start_seconds:.3f}",
        "end_seconds": f"{start_seconds + (duration_seconds or 0):.3f}",
        "deepgram_model": config.deepgram_model,
        "llm_provider": config.llm_provider,
        "llm_model": payload["models"]["llm_model"],
        "segment_count": str(len(completed)),
        "avg_confidence": f"{avg_conf:.4f}",
        "chrf_score": "" if chrf_score is None else f"{chrf_score:.3f}",
        "xcomet_score": "",
        "glossary_adherence_rate": (
            "" if glossary_result.rate is None else f"{glossary_result.rate:.3f}"
        ),
        "glossary_expected_terms": str(glossary_result.expected_terms),
        "glossary_matched_terms": str(glossary_result.matched_terms),
        "notes": (
            f"avg_latency={avg_latency:.2f}s max_latency={max_latency:.2f}s "
            f"avg_mt_latency={avg_mt_latency:.2f}s; {notes or ''}"
        ).strip()
        if avg_latency is not None
        else (notes or ""),
        "json_path": str(json_path),
    }

    _ensure_csv_header(csv_path, header)

    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a local-video segment experiment with true wall-clock "
            "latency measurement."
        )
    )
    parser.add_argument(
        "--input", required=True, help="Path to local video/audio file"
    )
    parser.add_argument("--start", default="0", help='e.g. "600" or "10:00"')
    parser.add_argument(
        "--duration", default=None, help="Seconds to process (default: rest of file)"
    )
    parser.add_argument("--name", default="video_clip")
    parser.add_argument("--domain", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--reference-ja", type=Path, default=None)
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1.0 = realtime playback pace (required for honest latency numbers).",
    )
    parser.add_argument("--endpointing", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    start_seconds = _parse_time(args.start)
    duration_seconds = _parse_time(args.duration) if args.duration else None

    json_path, csv_path = asyncio.run(
        run_experiment(
            input_path=args.input,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            experiment_name=args.name,
            domain=args.domain,
            notes=args.notes,
            reference_text_path=args.reference_ja,
            chunk_ms=args.chunk_ms,
            speed=args.speed,
            endpointing=args.endpointing,
        )
    )

    print(f"Wrote: {json_path}")
    print(f"Updated: {csv_path}")


if __name__ == "__main__":
    main()

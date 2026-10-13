"""Run a reproducible YouTube-segment transcription/translation experiment.

This runner:
- Resolves a YouTube URL to a direct audio URL (no full download required)
- Uses ffmpeg to decode only a time range to 16kHz mono PCM
- Streams PCM through the existing Deepgram+LLM pipeline
- Records ONLY final ASR + translation results
- Writes an experiment JSON and appends a row to experiments/results.csv

Usage (default runs 10:00–20:00 of the sample URL):
  uv sync --extra experiments
  uv run real-time-translation-exp-youtube

Notes:
- Requires ffmpeg installed on the host.
- You are responsible for ensuring you have rights to process the content.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from real_time_translation.audio.capture import QueueAudioCapture
from real_time_translation.config import Config
from real_time_translation.experiments.glossary_metrics import (
    compute_glossary_adherence,
)
from real_time_translation.experiments.media import (
    parse_time,
    read_all_stderr,
    run_ffmpeg_pcm,
)
from real_time_translation.experiments.results_io import (
    ensure_csv_header,
    maybe_compute_chrf,
    slugify,
)
from real_time_translation.pipeline import TranslationPipeline, TranslationResult

DEFAULT_URL = "https://www.youtube.com/watch?v=JycsHP-sGmw"



async def _resolve_youtube_audio_url(url: str) -> str:
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Run: uv sync --extra experiments"
        ) from exc

    def _extract() -> str:
        ydl_opts: dict[str, Any] = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict) or "url" not in info:
            raise RuntimeError("Failed to resolve a direct audio URL via yt-dlp")
        return str(info["url"])

    return await asyncio.to_thread(_extract)


@dataclass(frozen=True)
class SegmentRecord:
    start_time: float | None
    end_time: float | None
    asr: str
    translation: str
    confidence: float
    kept_terms: list[str]



async def run_experiment(
    *,
    url: str,
    start_seconds: float,
    end_seconds: float,
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
    if domain and domain not in config.domain_packs:
        pack_path = config.domain_packs_dir / f"{domain}.csv"
        if pack_path.exists():
            config.domain_packs = [*config.domain_packs, domain]
    capture = QueueAudioCapture(max_queue_size=2000)
    pipeline = TranslationPipeline(config=config, audio_capture=capture)

    records: list[SegmentRecord] = []

    def on_result(result: TranslationResult) -> None:
        if not result.is_final or not result.is_translation_complete:
            return
        if not result.is_utterance_end:
            # Soft-finalized mid-utterance chunk (see
            # deepgram_max_interim_duration) -- more text for this same
            # utterance is coming and will arrive as a later, larger
            # accumulated result. Recording this one too would duplicate
            # its text in full_asr/full_translation and inflate
            # segment_count.
            return
        if not result.translated_text:
            return
        records.append(
            SegmentRecord(
                start_time=result.start_time,
                end_time=result.end_time,
                asr=result.original_text,
                translation=result.translated_text,
                confidence=result.confidence,
                kept_terms=list(result.kept_terms),
            )
        )

    pipeline.set_callback(on_result)

    duration = max(0.0, end_seconds - start_seconds)
    if duration <= 0:
        raise ValueError("end time must be greater than start time")

    audio_url = await _resolve_youtube_audio_url(url)

    sample_rate = 16000
    channels = 1
    bytes_per_sample = 2
    chunk_bytes = int(sample_rate * channels * bytes_per_sample * (chunk_ms / 1000.0))
    if chunk_bytes <= 0:
        raise ValueError("chunk_ms too small")

    proc = await run_ffmpeg_pcm(
        source=audio_url,
        start_seconds=start_seconds,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
    )
    if proc.stdout is None:
        raise RuntimeError("Failed to capture ffmpeg stdout")

    stderr_task = asyncio.create_task(read_all_stderr(proc))

    await pipeline.start()
    try:
        while True:
            data = await proc.stdout.read(chunk_bytes)
            if not data:
                break
            capture.push_audio(data)

            if speed > 0:
                await asyncio.sleep((chunk_ms / 1000.0) / speed)

        await proc.wait()
        stderr_text = await stderr_task
        if proc.returncode and proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}):\n{stderr_text}")

        # Signal end of audio to prevent Deepgram timeout
        await pipeline._audio_capture.stop()
        await pipeline._transcriber.finalize()

        # Give the transcriber/translator time to flush remaining results.
        stable_seconds = 0
        last_count = len(records)
        for _ in range(180):
            await asyncio.sleep(1)
            if len(records) == last_count:
                stable_seconds += 1
            else:
                stable_seconds = 0
                last_count = len(records)
            if stable_seconds >= 15:
                break
    finally:
        await pipeline.stop()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    # Aggregate output text for optional metric
    full_asr = "\n".join(r.asr for r in records)
    full_translation = "\n".join(r.translation for r in records)

    chrf_score = maybe_compute_chrf(
        hypothesis=full_translation,
        reference_text_path=reference_text_path,
    )

    glossary_result = compute_glossary_adherence(
        pipeline._translator.dictionary,
        source_text=full_asr,
        hypothesis_text=full_translation,
    )

    avg_conf = (
        sum(r.confidence for r in records) / len(records) if records else 0.0
    )

    now = datetime.now(timezone.utc)
    date_str = now.date().isoformat()
    stamp = now.strftime("%Y%m%d")
    slug = slugify(experiment_name)

    experiments_dir = Path("experiments")
    experiments_dir.mkdir(parents=True, exist_ok=True)

    json_path = experiments_dir / f"{stamp}_{slug}.json"
    csv_path = experiments_dir / "results.csv"

    payload: dict[str, Any] = {
        "date": date_str,
        "experiment_name": experiment_name,
        "domain": domain,
        "input": {
            "type": "youtube",
            "url": url,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
        },
        "models": {
            "deepgram_model": config.deepgram_model,
            "llm_provider": config.llm_provider,
            "llm_model": config.gemini_model
            if config.llm_provider == "gemini"
            else config.openai_model,
        },
        "config": {
            "source_language": config.source_language,
            "target_language": config.target_language,
            "context_window_size": config.context_window_size,
            "translation_queue_size": config.translation_queue_size,
            "deepgram_endpointing": config.deepgram_endpointing,
            "deepgram_utterance_end_ms": config.deepgram_utterance_end_ms,
            "domain_packs": config.domain_packs,
            "dictionary_size": len(pipeline._translator.dictionary),
        },
        "results": {
            "segment_count": len(records),
            "avg_confidence": avg_conf,
            "segments": [asdict(r) for r in records],
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
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Append to CSV (create with header if missing)
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
        "url": url,
        "start_seconds": f"{start_seconds:.3f}",
        "end_seconds": f"{end_seconds:.3f}",
        "deepgram_model": config.deepgram_model,
        "llm_provider": config.llm_provider,
        "llm_model": payload["models"]["llm_model"],
        "segment_count": str(len(records)),
        "avg_confidence": f"{avg_conf:.4f}",
        "chrf_score": "" if chrf_score is None else f"{chrf_score:.3f}",
        "xcomet_score": "",
        "glossary_adherence_rate": (
            "" if glossary_result.rate is None else f"{glossary_result.rate:.3f}"
        ),
        "glossary_expected_terms": str(glossary_result.expected_terms),
        "glossary_matched_terms": str(glossary_result.matched_terms),
        "notes": notes or "",
        "json_path": str(json_path),
    }

    ensure_csv_header(csv_path, header)

    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    return json_path, csv_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a YouTube segment experiment (final ASR + translation → JSON/CSV)."
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--start", default="10:00", help='e.g. "600" or "10:00"')
    parser.add_argument("--end", default="20:00", help='e.g. "1200" or "20:00"')
    parser.add_argument("--name", default="youtube_10m_to_20m")
    parser.add_argument("--domain", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--reference-ja",
        type=Path,
        default=None,
        help="Optional reference translation text file for chrF scoring.",
    )
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="1.0 = realtime. 2.0 = 2x faster. 0 = no pacing (not recommended).",
    )
    parser.add_argument(
        "--endpointing",
        type=int,
        default=None,
        help="Override Deepgram endpointing threshold (ms).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    start_seconds = parse_time(args.start)
    end_seconds = parse_time(args.end)

    json_path, csv_path = asyncio.run(
        run_experiment(
            url=args.url,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
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

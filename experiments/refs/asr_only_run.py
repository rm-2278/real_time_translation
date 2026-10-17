"""Deepgram-only ASR run (no LLM translation) for pronunciation-failure analysis.

Bypasses the Gemini/OpenAI translation step entirely so it can run even when
no valid LLM API key is configured. Streams a YouTube segment's audio through
Deepgram and records only final transcription results to JSON.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from real_time_translation.config import Config  # noqa: E402
from real_time_translation.experiments.youtube_segment import (  # noqa: E402
    _parse_time,
    _read_all_stderr,
    _resolve_youtube_audio_url,
    _run_ffmpeg_pcm,
)
from real_time_translation.transcription.deepgram_client import (  # noqa: E402
    DeepgramTranscriber,
    TranscriptionResult,
)


@dataclass
class Rec:
    start_time: float
    end_time: float
    text: str
    confidence: float


async def main() -> None:
    url = sys.argv[1]
    start = _parse_time(sys.argv[2])
    end = _parse_time(sys.argv[3])
    out_name = sys.argv[4]
    endpointing = int(sys.argv[5]) if len(sys.argv) > 5 else None
    speed = float(sys.argv[6]) if len(sys.argv) > 6 else 5.0

    config = Config.from_env(require_zoom=False)
    if endpointing is not None:
        config.deepgram_endpointing = endpointing

    records: list[Rec] = []

    def on_result(r: TranscriptionResult) -> None:
        if not r.is_final or not r.text.strip():
            return
        records.append(Rec(r.start_time, r.end_time, r.text, r.confidence))

    transcriber = DeepgramTranscriber(
        api_key=config.deepgram_api_key,
        language=config.source_language,
        model=config.deepgram_model,
        smart_format=config.deepgram_smart_format,
        interim_results=config.deepgram_interim_results,
        endpointing=config.deepgram_endpointing,
        utterance_end_ms=config.deepgram_utterance_end_ms,
        vad_events=config.deepgram_vad_events,
    )
    transcriber.set_callback(on_result)

    duration = end - start
    audio_url = await _resolve_youtube_audio_url(url)
    sample_rate, channels, bps = 16000, 1, 2
    chunk_ms = 100
    chunk_bytes = int(sample_rate * channels * bps * (chunk_ms / 1000.0))

    proc = await _run_ffmpeg_pcm(
        input_url=audio_url,
        start_seconds=start,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
    )
    stderr_task = asyncio.create_task(_read_all_stderr(proc))

    await transcriber.connect()
    try:
        while True:
            data = await proc.stdout.read(chunk_bytes)
            if not data:
                break
            await transcriber.send_audio(data)
            if speed > 0:
                await asyncio.sleep((chunk_ms / 1000.0) / speed)
        await proc.wait()
        stderr_text = await stderr_task
        if proc.returncode and proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr_text}")
        await transcriber.finalize()
        await asyncio.sleep(3)
    finally:
        await transcriber.disconnect()

    out_path = Path(__file__).parent / f"{out_name}.json"
    out_path.write_text(
        json.dumps(
            {
                "url": url,
                "start": start,
                "end": end,
                "deepgram_model": config.deepgram_model,
                "endpointing": config.deepgram_endpointing,
                "segment_count": len(records),
                "segments": [asdict(r) for r in records],
                "full_text": " ".join(r.text for r in records),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({len(records)} segments)")


if __name__ == "__main__":
    asyncio.run(main())

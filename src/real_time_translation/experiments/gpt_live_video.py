"""Run a local video clip through OpenAI gpt-live-1 as a live interpreter.

gpt-live-1 is a full-duplex voice model (wss://api.openai.com/v1/live/sessions),
not an ASR/MT pair: we stream the clip's audio in at real playback speed, tell
it to interpret English into Japanese, and record the *output transcript*
text with its true arrival time. The result is written in the same JSON shape
as video_segment.py so caption_video_demo can burn it into a video.

Usage:
  uv run python -m real_time_translation.experiments.gpt_live_video \
    --input "video/LLM2025応用編_第8回前半_260128.mp4" \
    --start 10:00 --duration 180 --name gpt_live1_llm2025_3min
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import websockets
from dotenv import load_dotenv

from real_time_translation.experiments.media import parse_time, run_ffmpeg_pcm
from real_time_translation.experiments.results_io import ensure_csv_header, slugify

URL = "wss://api.openai.com/v1/live/sessions"
MODEL = "gpt-live-1"
RATE = 24000
INSTRUCTIONS = (
    "You are a simultaneous interpreter for a university lecture. The speaker "
    "talks in English. Interpret everything you hear into natural Japanese, "
    "sentence by sentence, as soon as each sentence is clear. Output only the "
    "Japanese interpretation. Never answer, comment, greet, or ask anything. "
    "Keep technical terms (e.g. Transformer, LLM) in their usual katakana or "
    "English form."
)
SENTENCE_END = re.compile(r"[。！？!?]+")


async def run(args: argparse.Namespace) -> dict:
    key = os.environ["OPENAI_API_KEY"]
    start_s = parse_time(args.start)
    proc = await run_ffmpeg_pcm(
        source=args.input,
        start_seconds=start_s,
        duration_seconds=args.duration,
        sample_rate=RATE,
        channels=1,
    )
    events: list[dict] = []
    raw_types: dict[str, int] = {}
    usage: dict = {}
    t0 = time.monotonic()
    sent_end = asyncio.Event()

    async with websockets.connect(
        URL, additional_headers={"Authorization": f"Bearer {key}"}, max_size=None
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.start",
                    "session": {
                        "model": MODEL,
                        "instructions": INSTRUCTIONS,
                        "audio": {
                            "format": {"type": "audio/pcm", "rate": RATE},
                            "output": {"voice": "marin"},
                        },
                    },
                }
            )
        )
        while json.loads(await ws.recv()).get("type") != "session.started":
            pass
        t0 = time.monotonic()

        async def sender() -> None:
            chunk = RATE * 2 // 10  # 100 ms of s16le mono
            sent = 0
            assert proc.stdout is not None
            while data := await proc.stdout.read(chunk):
                if len(data) % 2:
                    data += b"\0"
                await ws.send(
                    json.dumps(
                        {
                            "type": "session.input_audio.append",
                            "audio": base64.b64encode(data).decode(),
                        }
                    )
                )
                sent += len(data)
                # pace to real time
                await asyncio.sleep(
                    max(0.0, sent / 2 / RATE / args.speed - (time.monotonic() - t0))
                )
            sent_end.set()

        async def receiver() -> None:
            nonlocal usage
            try:
                await _receive()
            except websockets.ConnectionClosed as exc:
                print("connection closed:", exc)

        async def _receive() -> None:
            nonlocal usage
            async for msg in ws:
                ev = json.loads(msg)
                t = ev.get("type", "")
                raw_types[t] = raw_types.get(t, 0) + 1
                now = time.monotonic() - t0
                if t in (
                    "session.input_transcript.delta",
                    "session.output_transcript.delta",
                ):
                    events.append({"t": now, "type": t, "delta": ev.get("delta", "")})
                elif t == "error":
                    print("ERROR", ev)
                elif t not in ("session.output_audio.delta",):
                    print("EVENT", str(ev)[:300])
                elif t == "session.closed":
                    usage = ev
                    return

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        await send_task
        await asyncio.sleep(args.tail)  # let the interpreter finish speaking
        await ws.send(json.dumps({"type": "session.close"}))
        await asyncio.wait_for(recv_task, timeout=30)
    await proc.wait()
    return {"events": events, "raw_types": raw_types, "usage": usage}


def to_experiment_events(raw: list[dict], gap: float) -> list[dict]:
    """Group transcript deltas into sentences -> translation_complete events."""
    out: list[dict] = []
    prev_end = 0.0

    def emit(buf: list[dict], src: str) -> None:
        nonlocal prev_end
        text = "".join(d["delta"] for d in buf).strip()
        if not text:
            return
        end = buf[-1]["t"]
        out.append(
            {
                "kind": "translation_complete",
                "playback_offset": end,
                "wall_clock_lag": 0.0,
                "text": text,
                "is_final": True,
                "asr_start_time": prev_end,
                "asr_end_time": end,
                "confidence": 0.0,
                "original_text": src,
                "is_utterance_end": True,
            }
        )
        prev_end = end

    buf: list[dict] = []
    src_buf = ""
    for d in raw:
        if d["type"] == "session.input_transcript.delta":
            src_buf += d["delta"]
            continue
        if buf and d["t"] - buf[-1]["t"] > gap:
            emit(buf, src_buf)
            buf, src_buf = [], ""
        # A delta can span a sentence boundary: emit up to each terminator
        # at this delta's arrival time and carry the remainder forward.
        rest = d["delta"]
        while (m := SENTENCE_END.search(rest)) and m.end() <= len(rest):
            buf.append({"t": d["t"], "delta": rest[: m.end()]})
            emit(buf, src_buf)
            buf, src_buf, rest = [], "", rest[m.end() :]
        if rest:
            buf.append({"t": d["t"], "delta": rest})
    if buf:
        emit(buf, src_buf)
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--start", default="0")
    p.add_argument("--duration", type=float, default=180.0)
    p.add_argument("--name", default="gpt_live1_clip")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--tail", type=float, default=15.0)
    p.add_argument(
        "--gap", type=float, default=1.5, help="silence (s) ending a sentence"
    )
    p.add_argument("--notes", default=None)
    args = p.parse_args(argv)
    load_dotenv()

    res = asyncio.run(run(args))
    events = to_experiment_events(res["events"], args.gap)
    start_s = parse_time(args.start)
    now = datetime.now(UTC)
    stamp, slug = now.strftime("%Y%m%d"), slugify(args.name)
    exp_dir = Path("experiments")
    json_path = exp_dir / f"{stamp}_{slug}.json"
    full = "".join(e["text"] for e in events)
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "experiment_name": args.name,
        "domain": None,
        "input": {
            "type": "local_video",
            "path": args.input,
            "start_seconds": start_s,
            "duration_seconds": args.duration,
        },
        "models": {"llm_provider": "openai-live", "llm_model": MODEL},
        "config": {"instructions": INSTRUCTIONS, "speed": args.speed, "gap": args.gap},
        "results": {
            "segment_count": len(events),
            "segments": [],
            "raw_transcript_deltas": res["events"],
            "events": events,
            "full_translation": full,
            "raw_event_counts": res["raw_types"],
            "usage": res["usage"],
        },
        "metrics": {},
        "notes": args.notes,
        "created_at_utc": now.isoformat(),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    csv_path = exp_dir / "results.csv"
    header = next(csv.reader(csv_path.open(encoding="utf-8")))
    ensure_csv_header(csv_path, header)
    row = {
        "date": payload["date"],
        "experiment_name": args.name,
        "url": args.input,
        "start_seconds": f"{start_s:.3f}",
        "end_seconds": f"{start_s + args.duration:.3f}",
        "llm_provider": "openai-live",
        "llm_model": MODEL,
        "segment_count": len(events),
        "notes": args.notes or "",
        "json_path": str(json_path),
    }
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=header).writerow(row)
    print(f"Wrote: {json_path}  ({len(events)} sentences)")
    print("event types:", res["raw_types"])


if __name__ == "__main__":
    main()
